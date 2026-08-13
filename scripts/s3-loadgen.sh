#!/bin/bash
################################################################################
# s3-loadgen.sh
#
# S3 REST traffic exerciser for VAST.
# Default: AWS profile elbencho, endpoint http://172.200.202.2, bucket
# kmacs-elbencho-test.
#
# Lights up GET/PUT/DELETE/HEAD/LIST and multipart that opstat --s3 tracks.
################################################################################

set -u

AWS_PROFILE="${AWS_PROFILE:-elbencho}"
S3_ENDPOINT="${S3_ENDPOINT:-http://172.200.202.2}"
S3_BUCKET="${S3_BUCKET:-kmacs-elbencho-test}"
S3_PREFIX="${S3_PREFIX:-opstat-s3-loadgen}"
export AWS_PROFILE
export AWS_ENDPOINT_URL_S3="$S3_ENDPOINT"
export AWS_EC2_METADATA_DISABLED=true

if [ "$EUID" -eq 0 ] && [ -d /home/vastdata/.aws ]; then
  export HOME=/home/vastdata
  export AWS_SHARED_CREDENTIALS_FILE=/home/vastdata/.aws/credentials
  export AWS_CONFIG_FILE=/home/vastdata/.aws/config
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "[!] Error: aws CLI is not installed."
  exit 1
fi

if ! aws s3 ls "s3://$S3_BUCKET" >/dev/null 2>&1; then
  echo "[!] Error: cannot list s3://$S3_BUCKET with profile $AWS_PROFILE endpoint $S3_ENDPOINT"
  exit 1
fi

AWS_ACCESS_KEY_ID="$(aws configure get aws_access_key_id --profile "$AWS_PROFILE" || true)"
AWS_SECRET_ACCESS_KEY="$(aws configure get aws_secret_access_key --profile "$AWS_PROFILE" || true)"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

echo "======================================================================"
echo " LAUNCHING VAST S3 PROTOCOL FEATURE & TRAFFIC EXERCISER               "
echo "======================================================================"
echo " -> Profile:   $AWS_PROFILE"
echo " -> Endpoint:  $S3_ENDPOINT"
echo " -> Bucket:    $S3_BUCKET"
echo " -> Prefix:    $S3_PREFIX/"
echo " -> Exercising: GET PUT DELETE HEAD LIST MULTIPART"
echo "----------------------------------------------------------------------"
echo " [+] RUNNING FOREVER. Stop with: systemctl stop s3-loadgen"
echo "======================================================================"

PIDS=""

cleanup() {
  echo -e "\n\n[!] Caught stop signal. Cleaning up background traffic loops..."
  trap - INT TERM
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null
  # shellcheck disable=SC2086
  wait $PIDS 2>/dev/null
  aws s3 rm "s3://$S3_BUCKET/$S3_PREFIX/" --recursive >/dev/null 2>&1 || true
  echo "[+] All stress testing loops terminated cleanly. Exiting."
  exit 0
}
trap cleanup INT TERM

echo "[+] Starting LIST / HEAD metadata loop..."
while true; do
  aws s3 ls "s3://$S3_BUCKET/$S3_PREFIX/" >/dev/null 2>&1 || true
  aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "$S3_PREFIX/" --max-keys 20 >/dev/null 2>&1 || true
  aws s3api head-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1 || true
  sleep 0.5
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting PUT/GET/DELETE object loop..."
while true; do
  key="$S3_PREFIX/churn-$RANDOM.bin"
  dd if=/dev/urandom bs=64k count=4 status=none 2>/dev/null \
    | aws s3 cp - "s3://$S3_BUCKET/$key" >/dev/null 2>&1 || true
  aws s3api head-object --bucket "$S3_BUCKET" --key "$key" >/dev/null 2>&1 || true
  aws s3 cp "s3://$S3_BUCKET/$key" - >/dev/null 2>&1 || true
  aws s3 rm "s3://$S3_BUCKET/$key" >/dev/null 2>&1 || true
  sleep 0.2
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

echo "[+] Starting multipart PUT loop..."
while true; do
  tmp=$(mktemp)
  dd if=/dev/urandom of="$tmp" bs=1M count=8 status=none 2>/dev/null
  aws s3 cp "$tmp" "s3://$S3_BUCKET/$S3_PREFIX/mpu-$RANDOM.bin" \
    --expected-size 8388608 >/dev/null 2>&1 || true
  rm -f "$tmp"
  sleep 2
done >/dev/null 2>&1 &
PIDS="$PIDS $!"

if command -v elbencho >/dev/null 2>&1; then
  echo "[+] Spawning elbencho S3 engine (GET/PUT RPS)..."
  while true; do
    rm -f /tmp/s3-loadgen-elbencho.csv
    elbencho \
      --s3endpoints "$S3_ENDPOINT" \
      --s3objprefix "${S3_PREFIX}-eb-" \
      -t 8 -n 2 -N 20 -s 1m -b 1m \
      --timelimit 60 --s3-rps --nolive \
      --csvfile /tmp/s3-loadgen-elbencho.csv \
      -w \
      "$S3_BUCKET" >>/tmp/s3-loadgen-elbencho.log 2>&1
    sleep 1
  done &
  PIDS="$PIDS $!"
else
  echo "[!] elbencho not found; continuing with aws CLI loops only."
fi

echo "----------------------------------------------------------------------"
echo " ALL WORKLOADS ACTIVE. Watch with: ./opstat --s3"
echo "----------------------------------------------------------------------"

while true; do
  sleep 1
done
