PROJECT=$(gcloud config get project)

gcloud iam service-accounts create cykubed-agent --project="$PROJECT"

kubectl create serviceaccount cykubed-agent --namespace cykubed

gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:cykubed-agent@$PROJECT.iam.gserviceaccount.com" --role "roles/logging.logWriter"

gcloud iam service-accounts add-iam-policy-binding cykubed-agent@"$PROJECT".iam.gserviceaccount.com --role "roles/iam.workloadIdentityUser" --member "serviceAccount:$PROJECT.svc.id.goog[cykubed/cykubed-agent]"

kubectl annotate serviceaccount cykubed-agent --namespace cykubed  iam.gke.io/gcp-service-account=cykubed-agent@$PROJECT.iam.gserviceaccount.com
