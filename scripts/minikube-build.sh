eval "$(minikube docker-env)"
docker build  . -t nickbrookck/cykube-agent:$1





