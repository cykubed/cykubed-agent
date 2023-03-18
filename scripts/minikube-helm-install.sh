eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$2
helm upgrade --install cykube -n cykube --create-namespace --set token=$1 --set agentName=Minikube --set tag="$2" --set apiUrl="https://dev.cykube.net/api" ./chart
kubectl rollout status deployment cykube-agent
kubectl rollout status statefulsets fs




