import crud
from models import get_db
from tasks import clone_and_build

if __name__ == '__main__':
    db = next(get_db())
    crud.cancel_previous_test_runs(db, 'master')
    clone_and_build('kisanhubcore/kisanhub-webapp', 'e88380e2c5060121e976f2106459e71aaea02e2d',
                    'master')
