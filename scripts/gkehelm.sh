helm upgrade --install cykube -n cykube --create-namespace --set token=$1 --set agentName=GKE-S --set tag="$2" --set apiUrl="https://api.cykube.net" ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets fs
