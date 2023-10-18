#!/bin/bash
set -e

EXTRA_ARGS=" --set redis.architecture=standalone --set redis.sentinel.enabled=false --set architecture=standalone"
NAMESPACE=cykubed
TOKEN=$(mysql cykubedmain -e "select token from agent where platform='minikube' limit 1" --skip-column-names --silent --raw)

while getopts "rn:t:" opt; do
  case $opt in
     r)
       EXTRA_ARGS=""
       ;;
     n)
       NAMESPACE=$OPTARG
       ;;
     t)
       TOKEN=$OPTARG
       ;;
  esac
done

TAG=$(poetry version patch -s)
docker build  . -t us-docker.pkg.dev/cykubed/public/agent:"$TAG"

if [ -z "$TOKEN" ]; then
  echo "Token not found"
  exit 1
fi

echo "Building Agent with version $TAG"

minikube image load us-docker.pkg.dev/cykubed/public/agent:"$TAG"
helm package ./chart -d /tmp/agent-dist --app-version "$TAG" --version "$TAG"
helm upgrade --install $NAMESPACE -n $NAMESPACE --create-namespace --set token="$TOKEN" --set tag="$TAG" --set apiUrl="https://dev.cykubed.com/api" --force --set platform=minikube --set imagePullPolicy=IfNotPresent $EXTRA_ARGS /tmp/agent-dist/agent-$TAG.tgz
kubectl config set-context --current --namespace=$NAMESPACE
kubectl rollout status statefulsets agent
