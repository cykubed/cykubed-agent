VERSION=$(cat /workspace/version.txt)
rm -fr build/*
mkdir -p build/chart
mkdir -p build/dist
cp -fr chart/* build/chart
sed -i "s/IMAGE_TAG/$VERSION/g" build/chart/values.yaml
helm package build/chart -d build/dist --app-version "$VERSION" --version "$VERSION"
