eval "$(minikube docker-env)"
docker build  . -t europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:$2
helm upgrade --install cykube -n cykube --create-namespace --set token=$1 --set tag="$2" --set apiUrl="https://dev.cykubed.com/api"  --set cache.storageClass=standard ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets agent






