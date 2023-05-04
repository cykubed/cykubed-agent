PROJECT=$(gcloud config get project)

gcloud iam service-accounts create cykubed --project="$PROJECT"

#kubectl create serviceaccount cykubed --namespace cykubed

gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:cykubed@$PROJECT.iam.gserviceaccount.com" --role "roles/logging.logWriter"

gcloud iam service-accounts add-iam-policy-binding cykubed@"$PROJECT".iam.gserviceaccount.com --role "roles/iam.workloadIdentityUser" --member "serviceAccount:$PROJECT.svc.id.goog[cykubed/cykubed]"

kubectl annotate serviceaccount cykubed --namespace cykubed  iam.gke.io/gcp-service-account=cykubed@$PROJECT.iam.gserviceaccount.com
