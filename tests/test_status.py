from k8 import check_is_spot


def test_check_spot():
    annotation = {
        'autopilot.gke.io/selector-toleration': '{"inputTolerations":[{"key":"kubernetes.io/arch","operator":"Equal","value":"amd64","effect":"NoSchedule"}],"outputTolerations":[{"key":"kubernetes.io/arch","operator":"Equal","value":"amd64","effect":"NoSchedule"},{"key":"cloud.google.com/gke-spot","operator":"Equal","value":"true","effect":"NoSchedule"}],"modified":true}'}
    assert check_is_spot(annotation) is True
