#!/usr/bin/env bash
# Publishes release files through a private S3 bucket + CloudFront.
#
#   ./publish_aws.sh tgclient-macos-arm64.dmg [more files...]
#
# First run creates (once) a private bucket with a random name, an Origin Access Control and a
# CloudFront distribution, and remembers them in ~/.tgclient-aws. Links look like
# https://d1abcdefgh.cloudfront.net/<file>: no account, bucket or region in them.
# Every run uploads the files plus SHA256SUMS and refreshes CloudFront's cache.
#
# Needs: aws CLI v2 with credentials (aws configure), openssl. Env: AWS_REGION (eu-central-1).
set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
STATE="${TGC_AWS_STATE:-$HOME/.tgclient-aws}"
[ $# -gt 0 ] || { echo "usage: $0 FILE..." >&2; exit 1; }
for f in "$@"; do [ -f "$f" ] || { echo "no such file: $f" >&2; exit 1; }; done

if [ -f "$STATE" ]; then
  # shellcheck source=/dev/null
  . "$STATE"
else
  BUCKET="tgc-dist-$(openssl rand -hex 6)"
  echo "Creating private bucket $BUCKET in $REGION"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

  echo "Creating CloudFront distribution"
  OAC_ID="$(aws cloudfront create-origin-access-control --origin-access-control-config \
    "Name=$BUCKET,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
    --query OriginAccessControl.Id --output text)"
  config="$(mktemp)"
  cat > "$config" <<EOF
{
  "CallerReference": "$BUCKET",
  "Comment": "",
  "Enabled": true,
  "PriceClass": "PriceClass_100",
  "Origins": {"Quantity": 1, "Items": [{
    "Id": "s3",
    "DomainName": "$BUCKET.s3.$REGION.amazonaws.com",
    "OriginAccessControlId": "$OAC_ID",
    "S3OriginConfig": {"OriginAccessIdentity": ""}
  }]},
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3",
    "ViewerProtocolPolicy": "redirect-to-https",
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "Compress": false,
    "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                       "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}}
  }
}
EOF
  read -r DIST_ID DIST_ARN DOMAIN < <(aws cloudfront create-distribution \
    --distribution-config "file://$config" \
    --query '[Distribution.Id, Distribution.ARN, Distribution.DomainName]' --output text)
  rm -f "$config"

  # Only this CloudFront distribution may read the bucket.
  aws s3api put-bucket-policy --bucket "$BUCKET" --region "$REGION" --policy "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Principal\": {\"Service\": \"cloudfront.amazonaws.com\"},
      \"Action\": \"s3:GetObject\",
      \"Resource\": \"arn:aws:s3:::$BUCKET/*\",
      \"Condition\": {\"StringEquals\": {\"AWS:SourceArn\": \"$DIST_ARN\"}}
    }]
  }"
  printf 'BUCKET=%s\nREGION=%s\nDIST_ID=%s\nDOMAIN=%s\n' "$BUCKET" "$REGION" "$DIST_ID" \
    "$DOMAIN" > "$STATE"
  echo "Saved to $STATE. The first deployment of CloudFront takes 5-15 minutes."
fi

sums="$(mktemp)"
for f in "$@"; do
  name="$(basename "$f")"
  case "$name" in
    *.dmg) type="application/x-apple-diskimage" ;;
    *) type="application/octet-stream" ;;
  esac
  aws s3 cp "$f" "s3://$BUCKET/$name" --region "$REGION" --no-progress \
    --content-type "$type" --content-disposition "attachment; filename=\"$name\""
  if command -v sha256sum >/dev/null; then hash="$(sha256sum "$f" | cut -d' ' -f1)"
  else hash="$(shasum -a 256 "$f" | cut -d' ' -f1)"; fi
  echo "$hash  $name" >> "$sums"
done
aws s3 cp "$sums" "s3://$BUCKET/SHA256SUMS" --region "$REGION" --no-progress \
  --content-type "text/plain"
rm -f "$sums"
# Same file names = same links: drop the cached old versions.
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

echo
for f in "$@"; do echo "https://$DOMAIN/$(basename "$f")"; done
echo "https://$DOMAIN/SHA256SUMS"
