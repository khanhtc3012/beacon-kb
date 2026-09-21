#!/usr/bin/env bash
# Deploy the daily sync job to AWS (ECS Fargate started by EventBridge Scheduler)
# and publish its run log on a public S3 page.
#
# Run it in AWS CloudShell, from a clone of this repository:
#
#     bash part1-ingest/deploy/aws.sh <command>
#
#   up             build and push the image, create everything, register the daily schedule
#   run            run the job once, wait for it, print the exit code and the log
#   test-schedule  start a one-time schedule 3 minutes from now (proves EventBridge starts the task)
#   publish        copy the recent job logs to a public S3 page and print the link
#   status         show what exists now
#   down           delete everything, including the bucket
#
# Settings (environment variables, all optional):
#   ARTICLE_LIMIT      first N articles only (default 30). Set it to an empty value for all articles.
#   VECTOR_STORE_NAME  OpenAI vector store the job syncs to (default support-kb)
#   SCHEDULE_CRON, SCHEDULE_TZ   when the job runs (default 02:00 Asia/Ho_Chi_Minh)
#   LOG_WINDOW         how far back `publish` reads the logs (default 72h)
#   DEPLOY_REGION      AWS region (default ap-southeast-1)
set -euo pipefail

# Fixed names and region on purpose: generic variables such as NAME or AWS_REGION are often already set
# in a shell (CloudShell sets AWS_REGION to the console's region), which once put a resource in the wrong region.
NAME=beacon-kb
REGION=${DEPLOY_REGION:-ap-southeast-1}
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION
ARTICLE_LIMIT=${ARTICLE_LIMIT-30}
VECTOR_STORE_NAME=${VECTOR_STORE_NAME:-support-kb}
SCHEDULE_CRON=${SCHEDULE_CRON:-cron(0 2 * * ? *)}
SCHEDULE_TZ=${SCHEDULE_TZ:-Asia/Ho_Chi_Minh}
LOG_WINDOW=${LOG_WINDOW:-72h}
REPO_URL=${REPO_URL:-https://github.com/khanhtc3012/beacon-kb}

APP_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
EXEC_ROLE=$NAME-task-exec
SCHED_ROLE=$NAME-scheduler
PARAM=/$NAME/openai-api-key
LOG_GROUP=/ecs/$NAME
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
have() { "$@" >/dev/null 2>&1; }

init() {
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || die "not logged in to AWS"
  IMAGE=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$NAME:latest
  # a stable bucket name that does not contain the account id
  BUCKET=$NAME-logs-$(printf %s "$ACCOUNT_ID" | sha256sum | cut -c1-8)
  echo "region=$REGION account=${ACCOUNT_ID:0:4}********"
}

network() {
  VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  [ "$VPC" != None ] || die "this account has no default VPC"
  SUBNET=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text)
  SG=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC" Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' --output text)
  [ "$SUBNET" != None ] && [ "$SG" != None ] || die "no default subnet or security group in $VPC"
}

# The scheduler target, shared by the daily schedule and the one-time test.
write_target() {
  cat > "$WORK/target.json" <<EOF
{"Arn":"arn:aws:ecs:$REGION:$ACCOUNT_ID:cluster/$NAME",
 "RoleArn":"arn:aws:iam::$ACCOUNT_ID:role/$SCHED_ROLE",
 "EcsParameters":{"TaskDefinitionArn":"arn:aws:ecs:$REGION:$ACCOUNT_ID:task-definition/$NAME","TaskCount":1,"LaunchType":"FARGATE",
  "NetworkConfiguration":{"awsvpcConfiguration":{"Subnets":["$SUBNET"],"SecurityGroups":["$SG"],"AssignPublicIp":"ENABLED"}}},
 "RetryPolicy":{"MaximumRetryAttempts":2,"MaximumEventAgeInSeconds":3600}}
EOF
}

ensure_role() { # ensure_role <name> <service principal>
  if have aws iam get-role --role-name "$1"; then return 0; fi
  cat > "$WORK/trust.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"$2"},"Action":"sts:AssumeRole"}]}
EOF
  aws iam create-role --role-name "$1" --assume-role-policy-document "file://$WORK/trust.json" >/dev/null
  echo "created role $1"
  NEW_ROLE=1
}

cmd_up() {
  init
  command -v docker >/dev/null || die "docker is not available in this shell"
  [ "$(uname -m)" = x86_64 ] || die "this shell is $(uname -m); the task is defined as X86_64, so the image must be built on x86_64"
  [ -f "$APP_DIR/Dockerfile" ] || die "run this from a clone of the repository (no Dockerfile in $APP_DIR)"

  say "1/8 image repository (keeps the last 2 images)"
  have aws ecr describe-repositories --repository-names "$NAME" || aws ecr create-repository --repository-name "$NAME" >/dev/null
  aws ecr put-lifecycle-policy --repository-name "$NAME" --lifecycle-policy-text \
    '{"rules":[{"rulePriority":1,"description":"keep the last 2 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":2},"action":{"type":"expire"}}]}' >/dev/null

  say "2/8 build and push the image"
  docker build -t "$NAME" "$APP_DIR"
  aws ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
  docker tag "$NAME:latest" "$IMAGE"
  docker push "$IMAGE"

  say "3/8 OpenAI key in SSM Parameter Store (encrypted)"
  if [ -z "${RESET_KEY:-}" ] && have aws ssm get-parameter --name "$PARAM"; then
    echo "already stored in $REGION (set RESET_KEY=1 to replace it)"
  else
    read -r -s -p "OpenAI API key (typing is hidden): " KEY; echo
    [ -n "$KEY" ] || die "empty key"
    (umask 077; printf %s "$KEY" > "$WORK/key")
    unset KEY
    aws ssm put-parameter --name "$PARAM" --type SecureString --value "file://$WORK/key" --overwrite >/dev/null
    rm -f "$WORK/key"
  fi
  aws ssm get-parameter --name "$PARAM" --query 'Parameter.[Name,Type]' --output text || die "the parameter is not in $REGION"

  say "4/8 log group (7 days)"
  aws logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true
  aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 7

  say "5/8 IAM roles"
  NEW_ROLE=""
  ensure_role "$EXEC_ROLE" ecs-tasks.amazonaws.com
  aws iam attach-role-policy --role-name "$EXEC_ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  cat > "$WORK/ssm-read.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ssm:GetParameters","Resource":"arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter$PARAM"}]}
EOF
  aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name read-openai-key --policy-document "file://$WORK/ssm-read.json"
  ensure_role "$SCHED_ROLE" scheduler.amazonaws.com
  cat > "$WORK/scheduler-perm.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"ecs:RunTask","Resource":"arn:aws:ecs:$REGION:$ACCOUNT_ID:task-definition/$NAME:*","Condition":{"ArnLike":{"ecs:cluster":"arn:aws:ecs:$REGION:$ACCOUNT_ID:cluster/$NAME"}}},
 {"Effect":"Allow","Action":"iam:PassRole","Resource":"arn:aws:iam::$ACCOUNT_ID:role/$EXEC_ROLE","Condition":{"StringLike":{"iam:PassedToService":"ecs-tasks.amazonaws.com"}}}
]}
EOF
  aws iam put-role-policy --role-name "$SCHED_ROLE" --policy-name run-task --policy-document "file://$WORK/scheduler-perm.json"
  [ -z "$NEW_ROLE" ] || { echo "waiting for IAM to settle"; sleep 12; }

  say "6/8 ECS cluster and task definition"
  aws ecs create-cluster --cluster-name "$NAME" >/dev/null
  ENV_JSON="{\"name\":\"VECTOR_STORE_NAME\",\"value\":\"$VECTOR_STORE_NAME\"}"
  [ -z "$ARTICLE_LIMIT" ] || ENV_JSON="$ENV_JSON,{\"name\":\"ARTICLE_LIMIT\",\"value\":\"$ARTICLE_LIMIT\"}"
  cat > "$WORK/taskdef.json" <<EOF
{
  "family": "$NAME", "cpu": "256", "memory": "512", "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
  "executionRoleArn": "arn:aws:iam::$ACCOUNT_ID:role/$EXEC_ROLE",
  "containerDefinitions": [{
    "name": "$NAME", "image": "$IMAGE", "essential": true,
    "secrets": [{"name": "OPENAI_API_KEY", "valueFrom": "arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter$PARAM"}],
    "environment": [$ENV_JSON],
    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "$LOG_GROUP", "awslogs-region": "$REGION", "awslogs-stream-prefix": "job"}}
  }]
}
EOF
  aws ecs register-task-definition --cli-input-json "file://$WORK/taskdef.json" --query 'taskDefinition.[family,revision,status]' --output text

  say "7/8 network (default VPC, public subnet, no NAT gateway)"
  network
  echo "subnet=$SUBNET sg=$SG"

  say "8/8 daily schedule: $SCHEDULE_CRON ($SCHEDULE_TZ)"
  write_target
  if have aws scheduler get-schedule --name "$NAME-daily"; then verb=update-schedule; else verb=create-schedule; fi
  aws scheduler "$verb" --name "$NAME-daily" --state ENABLED \
    --schedule-expression "$SCHEDULE_CRON" --schedule-expression-timezone "$SCHEDULE_TZ" \
    --flexible-time-window Mode=OFF --target "file://$WORK/target.json" --query ScheduleArn --output text

  cat <<EOF

Done. Next:
  bash $0 run            # run the job once and see the result
  bash $0 test-schedule  # prove the schedule starts the task (about 3 minutes)
  bash $0 publish        # public S3 link with the logs
EOF
}

cmd_run() {
  init; network
  say "starting the task"
  TASK=$(aws ecs run-task --cluster "$NAME" --launch-type FARGATE --task-definition "$NAME" \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --query 'tasks[0].taskArn' --output text)
  [ -n "$TASK" ] && [ "$TASK" != None ] || die "the task did not start"
  echo "task ${TASK##*/}"
  for _ in $(seq 1 120); do   # up to 30 minutes
    status=$(aws ecs describe-tasks --cluster "$NAME" --tasks "$TASK" --query 'tasks[0].lastStatus' --output text)
    [ "$status" = STOPPED ] && break
    sleep 15
  done
  [ "$status" = STOPPED ] || die "the task is still $status after 30 minutes"
  aws ecs describe-tasks --cluster "$NAME" --tasks "$TASK" \
    --query 'tasks[0].[stoppedReason,containers[0].exitCode,containers[0].reason]' --output text
  sleep 20   # CloudWatch can be a little late
  aws logs tail "$LOG_GROUP" --log-stream-names "job/$NAME/${TASK##*/}" --since 2h --format short || true
  code=$(aws ecs describe-tasks --cluster "$NAME" --tasks "$TASK" --query 'tasks[0].containers[0].exitCode' --output text)
  echo; echo "job exit code: $code"
  [ "$code" = 0 ]
}

cmd_test_schedule() {
  init; network; write_target
  when=$(date -u -d '+3 minutes' +%Y-%m-%dT%H:%M:%S)
  say "one-time schedule at $when UTC (deletes itself afterwards)"
  aws scheduler create-schedule --name "$NAME-once" --state ENABLED --action-after-completion DELETE \
    --schedule-expression "at($when)" --schedule-expression-timezone UTC \
    --flexible-time-window Mode=OFF --target "file://$WORK/target.json" --query ScheduleArn --output text
  echo "Wait about 5 minutes, then:  bash $0 publish"
}

# Refuse to publish anything that looks like a secret or an id of this account.
scan_log() {
  if grep -n -E "sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|arn:aws|amazonaws\.com|vs_[0-9a-f]{20,}|file-[A-Za-z0-9]{15,}|$ACCOUNT_ID" "$1"; then
    die "the log above looks sensitive; nothing was published"
  fi
}

build_index() { # build_index <log file> <output html>
  SCHEDULE_INFO=$(aws scheduler get-schedule --name "$NAME-daily" --query '[State,ScheduleExpression,ScheduleExpressionTimezone]' --output text 2>/dev/null || echo "not found")
  python3 - "$1" "$2" "$SCHEDULE_INFO" "$REPO_URL" "$NAME" <<'PY'
import datetime, html, re, sys
log_path, out_path, schedule, repo, name = sys.argv[1:6]
text = open(log_path, encoding="utf-8").read()
runs = []
for line in text.splitlines():
    m = re.search(r"^(\S+)\s+.*\[sync\] (.*)$", line)
    if m:
        runs.append((m.group(1), m.group(2)))
rows = "".join(f"<tr><td>{html.escape(t)}</td><td><code>{html.escape(s)}</code></td></tr>" for t, s in runs) \
    or "<tr><td colspan=2>no runs in this window</td></tr>"
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
page = f"""<!doctype html>
<meta charset="utf-8"><title>{html.escape(name)} job on AWS</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
pre{{background:#f4f4f4;padding:1rem;overflow:auto;font-size:13px}}</style>
<h1>{html.escape(name)}: daily sync job on AWS</h1>
<p>ECS Fargate task started by EventBridge Scheduler. Schedule (state, expression, time zone): <code>{html.escape(schedule)}</code>.
Code: <a href="{html.escape(repo)}">{html.escape(repo)}</a>. Page made {now}.</p>
<h2>Runs</h2>
<table><tr><th>Time</th><th>Summary</th></tr>{rows}</table>
<h2>Full log</h2>
<p><a href="last_run.log">last_run.log</a> (plain text)</p>
<pre>{html.escape(text)}</pre>
"""
open(out_path, "w", encoding="utf-8").write(page)
PY
}

cmd_publish() {
  init
  say "reading the logs of the last $LOG_WINDOW"
  aws logs tail "$LOG_GROUP" --since "$LOG_WINDOW" --format short > "$WORK/last_run.log"
  [ -s "$WORK/last_run.log" ] || die "no log lines in $LOG_GROUP for the last $LOG_WINDOW (run the job first)"
  wc -l < "$WORK/last_run.log" | xargs echo "log lines:"
  scan_log "$WORK/last_run.log"

  say "bucket $BUCKET"
  blocked=$(aws s3control get-public-access-block --account-id "$ACCOUNT_ID" \
    --query 'PublicAccessBlockConfiguration.[BlockPublicPolicy,RestrictPublicBuckets]' --output text 2>/dev/null || true)
  case "$blocked" in *True*|*true*) die "S3 Block Public Access is on for this whole account. Turn off 'Block public bucket policies' in S3 > Block Public Access settings for this account, then run publish again." ;; esac
  if ! have aws s3api head-bucket --bucket "$BUCKET"; then
    if [ "$REGION" = us-east-1 ]; then aws s3api create-bucket --bucket "$BUCKET" >/dev/null
    else aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null; fi
  fi
  # ACLs stay blocked; only a bucket policy for the public/ prefix can open it
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false
  aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
    '{"Rules":[{"ID":"expire-public-logs","Status":"Enabled","Filter":{"Prefix":"public/"},"Expiration":{"Days":30}}]}'
  aws s3api put-bucket-policy --bucket "$BUCKET" --policy \
    "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"PublicReadRunLogs\",\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/public/*\"}]}"

  build_index "$WORK/last_run.log" "$WORK/index.html"
  aws s3 cp "$WORK/last_run.log" "s3://$BUCKET/public/last_run.log" --content-type "text/plain; charset=utf-8" --cache-control no-cache >/dev/null
  aws s3 cp "$WORK/index.html" "s3://$BUCKET/public/index.html" --content-type "text/html; charset=utf-8" --cache-control no-cache >/dev/null

  base=https://$BUCKET.s3.$REGION.amazonaws.com/public
  cat <<EOF

Published. Give the interviewers this link:
  $base/index.html
Plain log:
  $base/last_run.log

Only these two files are public (the bucket cannot be listed) and they expire after 30 days.
Open the link in a private window to check it. Remove everything with:  bash $0 down
EOF
}

cmd_status() {
  init
  say "resources"
  have aws ecr describe-repositories --repository-names "$NAME" && echo "ecr repository:  yes" || echo "ecr repository:  no"
  have aws ssm get-parameter --name "$PARAM" && echo "ssm parameter:   yes" || echo "ssm parameter:   no"
  echo "ecs cluster:     $(aws ecs describe-clusters --clusters "$NAME" --query 'clusters[0].status' --output text 2>/dev/null || echo no)"
  echo "schedule:        $(aws scheduler get-schedule --name "$NAME-daily" --query '[State,ScheduleExpression]' --output text 2>/dev/null || echo no)"
  echo "roles:           $(have aws iam get-role --role-name "$EXEC_ROLE" && echo exec-yes || echo exec-no) $(have aws iam get-role --role-name "$SCHED_ROLE" && echo scheduler-yes || echo scheduler-no)"
  echo "bucket:          $(have aws s3api head-bucket --bucket "$BUCKET" && echo "yes ($BUCKET)" || echo no)"
}

cmd_down() {
  init
  read -r -p "Delete the job, its logs and the public bucket in $REGION? [y/N] " answer
  [ "$answer" = y ] || die "cancelled"
  step() { echo "- $1"; shift; "$@" >/dev/null 2>&1 || echo "  (nothing to delete, or already gone)"; }
  step "schedules" bash -c "aws scheduler delete-schedule --name $NAME-daily; aws scheduler delete-schedule --name $NAME-once"
  step "running tasks" bash -c "for t in \$(aws ecs list-tasks --cluster $NAME --query 'taskArns[]' --output text); do aws ecs stop-task --cluster $NAME --task \$t; done"
  step "task definitions" bash -c "for d in \$(aws ecs list-task-definitions --family-prefix $NAME --query 'taskDefinitionArns[]' --output text); do aws ecs deregister-task-definition --task-definition \$d; done"
  step "cluster" aws ecs delete-cluster --cluster "$NAME"
  step "image repository" aws ecr delete-repository --repository-name "$NAME" --force
  step "log group" aws logs delete-log-group --log-group-name "$LOG_GROUP"
  step "stored key" aws ssm delete-parameter --name "$PARAM"
  for role in "$EXEC_ROLE" "$SCHED_ROLE"; do
    step "role $role" bash -c "for p in \$(aws iam list-attached-role-policies --role-name $role --query 'AttachedPolicies[].PolicyArn' --output text); do aws iam detach-role-policy --role-name $role --policy-arn \$p; done; for p in \$(aws iam list-role-policies --role-name $role --query 'PolicyNames[]' --output text); do aws iam delete-role-policy --role-name $role --policy-name \$p; done; aws iam delete-role --role-name $role"
  done
  step "bucket $BUCKET" bash -c "aws s3 rm s3://$BUCKET --recursive; aws s3api delete-bucket --bucket $BUCKET"
  echo "done. Not touched: the OpenAI vector store and the OpenAI API key (clean those on the OpenAI side)."
}

case "${1:-}" in
  up) cmd_up ;;
  run) cmd_run ;;
  test-schedule) cmd_test_schedule ;;
  publish) cmd_publish ;;
  status) cmd_status ;;
  down) cmd_down ;;
  *) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
