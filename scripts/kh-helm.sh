#gcloud container clusters get-credentials europe --region europe-west2 --project kisanhub-uat
#gcloud config set project kisanhub-uat
helm upgrade --install cykube -n cykube --create-namespace --set token=$1 --set agentName=GKE-KH --set tag="$2" --set apiUrl="https://api.cykube.net" ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets fs
