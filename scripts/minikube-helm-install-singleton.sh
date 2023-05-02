TAG=$1
TOKEN=$(mysql cykubemain -e "select token from agent where name='minikube'" --skip-column-names --silent --raw)
docker build  . -t europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"
minikube image load europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:"$TAG"

helm upgrade --install cykube -n cykube --create-namespace --set token="$TOKEN" --set tag="$TAG" --set apiUrl="https://dev.cykubed.com/api"  --set cache.storageClass=standard --set redis.sentinel.enabled=false --set redis.architecture=standalone --set redis.replica.replicaCount=1  ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets agent






