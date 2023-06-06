minikube start #--cpus 8 --memory 24000
minikube addons enable metrics-server
minikube addons enable volumesnapshots
minikube addons enable csi-hostpath-driver
