helm upgrade --install cykube -n cykube --create-namespace --set token=$1 --set agentName=GKE --set tag="$2" --set apiUrl="https://dev.cykube.net/api" ./chart
kubectl rollout status deployment