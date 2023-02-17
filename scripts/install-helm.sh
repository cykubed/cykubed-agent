poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=f509dbd5-9f02-42bf-b225-1632a89a662d --set-string agentImage="nickbrookck/cykube-agent:$1" ./chart



