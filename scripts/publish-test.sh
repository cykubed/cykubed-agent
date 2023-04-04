rm -fr build/*
mkdir -p build/chart
mkdir -p build/dist
cp -fr chart/* build/chart
cp dev-values.yaml build/chart/values.yaml
VERSION=$(http POST https://dev.cykube.net/api/admin/bump-agent-minor-version -A bearer -a "$CYKUBE_DEV_API_TOKEN" -b)
sed -i "s/IMAGE_TAG/$COMMIT_SHA/g" build/chart/values.yaml
helm package build/chart -d build/dist --app-version "$VERSION" --version "$VERSION"
helm repo index build/dist --url https://charts.cykube.net
wrangler r2 object put charts/index.yaml --file=build/dist/index.yaml
wrangler r2 object put charts/agent-"$VERSION".tgz --file=build/dist/agent-"$VERSION".tgz
