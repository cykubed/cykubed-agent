TAG=${1:latest}
TOKEN=$(mysql cykubemain -e "select token from agent where name='minikube'" --skip-column-names --silent --raw)

helm upgrade --install cykubed cykubed/agent -n cykubed --create-namespace --set token="$TOKEN" --set tag="$TAG" --set apiUrl="https://dev.cykubed.com/api"  --set platform=minikube --set imagePullPolicy=IfNotPresent --set redis.sentinel.enabled=false --set redis.architecture=standalone  --set architecture=standalone
kubectl config set-context --current --namespace=cykubed
kubectl rollout status statefulsets agent






