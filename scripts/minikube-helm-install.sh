TAG=$1
TOKEN=$(mysql cykubemain -e "select token from agent where name='minikube'" --skip-column-names --silent --raw)
docker build  . -t europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"
minikube image load europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"
helm upgrade --install cykubed -n cykubed --create-namespace --set token="$TOKEN" --set tag="$TAG" --set apiUrl="https://dev.cykubed.com/api"  --set cache.storageClass=standard --set imagePullPolicy=IfNotPresent ./chart
kubectl config set-context --current --namespace=cykubed
kubectl rollout status statefulsets agent






