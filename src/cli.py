import crud
from models import get_db
from tasks import clone_and_build

if __name__ == '__main__':
    db = next(get_db())
    crud.cancel_previous_test_runs(db, 'master')
    clone_and_build('kisanhubcore/kisanhub-webapp', 'f23f1b352219aeb56ba1d45fa97190eee7883328',
                    'master')
