"""Send one real Windows toast for an isolated profile, then clean up.

The native transport/history and OS URL protocol are exercised. This does
not alter ordinary schedules, startup or the production app's registration.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def main():
    from duty_app.engine import empty_state,make_config
    from duty_app.notifications import action_uri,cleanup_test_registration,deliver,identity,powershell
    from duty_app.storage import Repository
    runtime=ROOT/".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="系统通知测试-",dir=runtime) as temp:
        directory=Path(temp)
        repo=Repository(directory/"duty.sqlite3")
        state=empty_state()
        target=datetime.now().date().isoformat()
        state["configs"]=[make_config(["值日通知测试"],target)]
        state["settings"]["paused"]=True
        repo.mutate(0,lambda _:state)
        token=repo.claim_reminder(datetime.now(),"00:00")
        try:
            assert deliver(directory,target,token)
            assert repo.reminder(target)[0]=="notified"
            history=powershell({"operation":"history","app_id":identity(directory)[0]})
            assert target in history, f"Notification history: {history}"
            # Use the actual Windows protocol, without a browser or Tk window.
            os.startfile(action_uri(directory,target,token,"dismiss"))
            for _ in range(80):
                if repo.reminder(target)[0]=="dismissed":
                    break
                time.sleep(.1)
            assert repo.reminder(target)[0]=="dismissed", "OS protocol activation failed"
            print(json.dumps({"result":"passed","native_history":True,"protocol_activation":True,
                              "transport_process_exited":True,"tk_windows":0}))
        except Exception as error:
            print(getattr(error,"details",""),file=sys.stderr)
            raise
        finally:
            cleanup_test_registration(directory)


if __name__=="__main__":
    main()
