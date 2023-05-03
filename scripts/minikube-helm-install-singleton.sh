TAG=$1
TOKEN=$(mysql cykubemain -e "select token from agent where name='minikube'" --skip-column-names --silent --raw)
docker build  . -t europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"
minikube image load europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"

helm upgrade --install cykubed -n cykubed --create-namespace --set token="$TOKEN" --set tag="$TAG" --set apiUrl="https://dev.cykubed.com/api"  --set platform=minikube --set imagePullPolicy=IfNotPresent --set cache.storageClass=standard --set redis.sentinel.enabled=false --set redis.architecture=standalone  --set architecture=standalone ./chart
kubectl config set-context --current --namespace=cykubed
kubectl rollout status statefulsets agent






