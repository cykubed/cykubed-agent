from datetime import datetime
from time import sleep

f = open('/var/lib/cypresshub/dist-cache/test.log', 'w')
i = 0
while True:
    dt = datetime.now()
    f.write(f'{i}: {dt.isoformat()} The quick brown fox jumped over the lazy hen. Again and again.\n')
    sleep(5)
    i += 1
    f.flush()
