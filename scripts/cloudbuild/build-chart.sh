VERSION=$(cat /workspace/version.txt)
mkdir -p /workspace/build/chart
mkdir -p /workspace/build/dist
cp -fr /workspace/chart/* /workspace/build/chart
sed -i "s/IMAGE_TAG/$VERSION/g" /workspace/build/chart/values.yaml
helm package /workspace/build/chart -d /workspace/build/dist --app-version "$VERSION" --version "$VERSION"
helm repo index /workspace/build/dist --url https://charts.cykubed.com
echo "{\"text\":\"Cykube agent $VERSION published\"}" > /workspace/notify.json

