#gcloud container clusters get-credentials europe --region europe-west2 --project kisanhub-uat
#gcloud config set project kisanhub-uat
helm upgrade --install cykube -n cykube --create-namespace --set token=15ad7b82-ecd5-476b-ad9d-1f6c6d38ed1d --set agentName=GKE-KH --set tag="$1" --set apiUrl="https://api.cykube.net" ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets fs
