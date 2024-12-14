TAG=$1
helm package ./chart -d ./dist --app-version "${TAG}" --version "${TAG}"
helm repo index ./dist --url https://charts.cykubed.com
wrangler r2 object put charts/index.yaml --file=./dist/index.yaml
wrangler r2 object put "charts/agent-${TAG}.tgz" --file=./dist/agent-${TAG}.tgz
http POST "https://api.cykubed.com/admin/image/agent/current-version/${TAG}" -A bearer -a ${CYKUBED_API_TOKEN}
echo "{\"text\":\"Cykubed agent published created with tag ${TAG}\"}" > payload.json
curl -X POST -H 'Content-type: application/json' --data "@payload.json" $SLACK_HOOK_URL
