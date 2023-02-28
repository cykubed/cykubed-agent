eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set token=$2 --set agentName=Minikube --set agentImage="nickbrookck/cykube-agent:$1" ./chart



