eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=$2 --set-string agentImage="nickbrookck/cykube-agent:$1" ./chart



