"""Exercise the actual campus UI in installed Chromium, without real user data."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.browser_smoke import CDP as BaseCDP, FakeStartup
from campus_app.server import AppServer
from campus_app.service import DutyService
from campus_app.storage import Repository


class CDP(BaseCDP):
    def wait(self, expression, timeout=12):
        # DOM nodes serialize as empty objects; ask Chromium for a boolean.
        return super().wait('Boolean('+expression+')', timeout)


def main():
    runtime = ROOT/".runtime"
    output = runtime/"qa"
    output.mkdir(parents=True, exist_ok=True)
    candidates = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))/"Google/Chrome/Application/chrome.exe",
                  Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))/"Microsoft/Edge/Application/msedge.exe"]
    browser = next(p for p in candidates if p.is_file())
    with tempfile.TemporaryDirectory(prefix="campus-browser-", dir=runtime) as temp:
        root = Path(temp)
        repo = Repository(root/"campus.sqlite3")
        service = DutyService(repo, today=lambda: date(2026, 10, 12), startup=FakeStartup())
        server = AppServer(("0.0.0.0", 0), service, ROOT, clock=lambda: datetime(2026, 10, 12, 22))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen([str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                    "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1", f"--user-data-dir={root/'profile'}", "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        cdp = None
        try:
            active = root/"profile/DevToolsActivePort"
            for _ in range(100):
                if active.exists(): break
                if process.poll() is not None: raise AssertionError("browser exited")
                time.sleep(.1)
            port = active.read_text().splitlines()[0]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/json", timeout=5) as reply:
                target = next(t for t in json.load(reply) if t["type"] == "page")
            cdp = CDP(target["webSocketDebuggerUrl"])
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            def size(width=1440, height=1100, mobile=False):
                cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
            def click(selector): cdp.evaluate(f"document.querySelector({json.dumps(selector)}).click()")
            def field(selector, value): cdp.evaluate(f"document.querySelector({json.dumps(selector)}).value={json.dumps(value)}")
            def submit(selector): cdp.evaluate(f"document.querySelector({json.dumps(selector)}).requestSubmit()")
            def check_no_overflow(): assert cdp.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), "horizontal page overflow"
            size()
            cdp.call("Page.navigate", {"url": server.dashboard_url()})
            cdp.wait("!document.querySelector('#wizard').hidden && document.querySelector('input[name=mode]')")
            cdp.screenshot(output/"campus-initialize.png")
            submit('#wizardForm')
            cdp.wait("document.querySelector('#rosterInput')")
            field('#className', '高一（1）班')
            field('#startDate', '2026-10-12')
            field('#rosterInput', '01 林予安\n02 陈星野\n03 沈知夏\n04 周亦辰\n05 许清和\n06 宋嘉宁')
            submit('#wizardForm')
            cdp.wait("document.querySelector('.area-editor')")
            click('[data-group-down]')
            assert cdp.evaluate("document.querySelector('[data-group-name]').value")=='第二组'
            click('[data-group-up]:not(:disabled)')
            assert cdp.evaluate("document.querySelector('[data-group-name]').value")=='第一组'
            click('#addArea')
            cdp.evaluate("const areas=document.querySelectorAll('.area-editor');areas[1].querySelector('[data-name]').value='走廊与公共区';areas[1].querySelector('[data-cycle]').value='weekly';areas[1].querySelector('[data-cycle]').dispatchEvent(new Event('change',{bubbles:true}));areas[1].querySelector('[data-policy]').value='calendar';areas[1].querySelectorAll('.group-editor').forEach((g,i)=>g.querySelectorAll('[data-member]').forEach((c,j)=>{c.checked=i===0?j<3:j>=3;}))")
            size(390,844,True)
            check_no_overflow()
            cdp.screenshot(output/"campus-mobile-areas.png")
            size()
            submit('#wizardForm')
            cdp.wait("document.querySelector('#setupTime')")
            submit('#wizardForm')
            cdp.wait("document.querySelector('#setupPassword')")
            field('#setupPassword','campus-password')
            field('#setupPasswordConfirm','campus-password')
            submit('#wizardForm')
            cdp.wait("!document.querySelector('#dashboard').hidden && document.querySelector('#classHeading').textContent==='高一（1）班'")
            cdp.wait("!document.querySelector('#overlapBanner').hidden")
            assert cdp.evaluate("document.querySelector('#markPrimary').getBoundingClientRect().top<100")
            click('#confirmOverlaps')
            cdp.wait("document.querySelector('#confirmDialog').open")
            click('#confirmYes')
            cdp.wait("document.querySelector('#overlapBanner').hidden")
            # Group penalty: only this area holds; an individual mark counts separately.
            click('[data-open=mark]')
            cdp.wait("document.querySelector('#markDialog').open")
            field('#markDays','3')
            submit('#markForm')
            cdp.wait("!document.querySelector('#markDialog').open && document.querySelector('#weekCount').textContent==='1'")
            self_state=repo.read()[1]
            first_group=self_state['configs'][0]['areas'][0]['groups'][0]
            assert service.view()['tomorrow_duty']['areas'][0]['group_id']==first_group['id']
            click('[data-open=mark]')
            field('#markKind','student')
            cdp.evaluate("document.querySelector('#markKind').dispatchEvent(new Event('change'))")
            field('#markPenalty','other')
            cdp.evaluate("document.querySelector('#markPenalty').dispatchEvent(new Event('change'))")
            field('#markNote','值日以外的考核备注')
            submit('#markForm')
            cdp.wait("!document.querySelector('#markDialog').open && document.querySelector('#weekCount').textContent==='2'")
            cdp.screenshot(output/"campus-dashboard.png")
            # Swap two unexecuted normal tasks, then cancel.
            click('[data-open=swap]')
            field('#swapFirst','2026-10-16')
            field('#swapSecond','2026-10-17')
            submit('#swapForm')
            cdp.wait("!document.querySelector('#swapDialog').open")
            assert len(repo.read()[1]['swaps'])==1
            click('[data-tab=students]')
            click('[data-cancel-swap]')
            cdp.wait("document.querySelector('#confirmDialog').open")
            click('#confirmYes')
            cdp.wait("document.querySelector('[data-cancel-swap]')===null")
            # Attendance return takes effect tomorrow, not today.
            first=first_group['student_ids'][0]
            click(f'[data-absence="{first}"]')
            cdp.wait(f"document.querySelector('[data-absence=\"{first}\"]').textContent==='返校'")
            click(f'[data-absence="{first}"]')
            cdp.wait(f"document.querySelector('[data-absence=\"{first}\"]').textContent==='待返校'")
            # Reminder settings use a simulated Run key implementation.
            click('#settingsOpen')
            field('#reminderInput','20:30')
            field('#reminderMode','display')
            cdp.evaluate("document.querySelector('#autostart').checked=true;document.querySelector('#wallpaperEnabled').checked=true")
            submit('#settingsForm')
            cdp.wait("!document.querySelector('#settingsDialog').open")
            assert repo.read()[1]['settings']['reminder_mode']=='display'
            assert service.startup.enabled()
            assert repo.read()[1]['settings']['wallpaper_enabled'] is True
            # Configuration preview and next-day effect, preserving today's class.
            click('#configOpen')
            submit('#wizardForm')
            cdp.wait("document.querySelector('#className')")
            field('#className','高一（1）班 · 校园')
            submit('#wizardForm')
            cdp.wait("document.querySelector('.area-editor')")
            submit('#wizardForm')
            cdp.wait("document.querySelector('#stepTitle').textContent==='核对保存'")
            submit('#wizardForm')
            cdp.wait("document.querySelector('#confirmDialog').open")
            click('#confirmYes')
            cdp.wait("document.querySelector('#wizard').hidden")
            assert service.view()['next_config']['effective']=='2026-10-13'
            assert service.view()['config']['class_name']=='高一（1）班'
            # Calendar and assessment views.
            click('[data-tab=calendar]')
            cdp.wait("document.querySelector('[data-day]')")
            click('#monthView')
            cdp.wait("document.querySelectorAll('[data-day]').length===42")
            cdp.screenshot(output/"campus-calendar.png")
            click('[data-day="2026-10-13"]')
            cdp.wait("document.querySelector('#dayDialog').open")
            click('#dayDialog [data-close]')
            click('[data-tab=assessment]')
            cdp.wait("document.querySelector('#reportSummary').textContent.includes('第一组')")
            assert len(service.report()['summary'])==2
            field('#reportTarget','group:'+first_group['id'])
            cdp.evaluate("document.querySelector('#reportTarget').dispatchEvent(new Event('change'))")
            cdp.wait("document.querySelector('#reportRange').textContent.includes('共 1 次')")
            field('#reportTarget','')
            cdp.evaluate("document.querySelector('#reportTarget').dispatchEvent(new Event('change'))")
            cdp.wait("document.querySelector('#reportRange').textContent.includes('共 2 次')")
            cdp.screenshot(output/"campus-assessments.png")
            # Mobile layout and a real dialog.
            size(390,844,True)
            check_no_overflow()
            cdp.screenshot(output/"campus-mobile-assessments.png")
            click('[data-tab=overview]')
            cdp.wait("!document.querySelector('#overviewPanel').hidden")
            check_no_overflow()
            cdp.screenshot(output/"campus-mobile.png")
            cdp.evaluate('window.scrollTo(0,document.body.scrollHeight)')
            assert cdp.evaluate("document.querySelector('#markPrimary').getBoundingClientRect().top>=0 && document.querySelector('#markPrimary').getBoundingClientRect().bottom<150"), 'assessment button did not stay visible'
            click('#markPrimary')
            cdp.wait("document.querySelector('#markDialog').open")
            click('#markDialog [data-close]')
            cdp.evaluate('window.scrollTo(0,0)')
            click('#wallpaperOpen')
            cdp.wait("document.querySelector('#settingsDialog').open")
            assert cdp.evaluate("document.activeElement.id==='wallpaperEnabled'")
            assert cdp.evaluate("document.querySelector('#wallpaperEnabled').checked")
            cdp.screenshot(output/"campus-mobile-wallpaper-settings.png")
            cdp.evaluate("document.querySelector('#wallpaperEnabled').checked=false")
            submit('#settingsForm')
            cdp.wait("!document.querySelector('#settingsDialog').open")
            assert repo.read()[1]['settings']['wallpaper_enabled'] is False
            click('[data-open=mark]')
            cdp.wait("document.querySelector('#markDialog').open")
            assert cdp.evaluate("document.querySelector('#markDialog').getBoundingClientRect().right<=390")
            cdp.screenshot(output/"campus-mobile-mark.png")
            click('#markDialog [data-close]')
            # Export/import use the actual HTTP routes.
            exported=cdp.evaluate("fetch('/api/export').then(r=>r.json())")
            click('[data-tab=students]')
            click('#importOpen')
            field('#importText',json.dumps(exported,ensure_ascii=False))
            submit('#importForm')
            cdp.wait("document.querySelector('#confirmDialog').open")
            click('#confirmYes')
            cdp.wait("!document.querySelector('#importDialog').open")
            click('#logout')
            cdp.wait("!document.querySelector('#loginScreen').hidden")
            field('#loginPassword','campus-password')
            submit('#loginForm')
            cdp.wait("!document.querySelector('#dashboard').hidden")
            # Remove admin authority before entering the separate display audience.
            cdp.call('Network.deleteCookies',{'name':'campus_session','url':f'http://127.0.0.1:{server.server_address[1]}/'})
            token=repo.claim_reminder(datetime(2026,10,12,22),'00:00')
            size()
            cdp.call('Page.navigate',{'url':server.display_url('2026-10-12',token)})
            cdp.wait("document.querySelectorAll('.display-area').length===2 && !document.querySelector('#reminderActions').hidden")
            assert cdp.evaluate("fetch('/api/state').then(r=>r.status)")==401
            assert '值日以外的考核备注' not in cdp.evaluate('document.body.innerText')
            assert repo.reminder('2026-10-12')[0]=='notified'
            cdp.screenshot(output/"campus-display.png")
            click('#displaySnooze')
            cdp.wait("document.querySelector('#reminderActions').hidden")
            assert repo.reminder('2026-10-12')[0]=='snoozed'
            size(390,844,True)
            check_no_overflow()
            cdp.screenshot(output/"campus-display-mobile.png")
            # Switch to individual mode through the real editor and resolve
            # remaining group penalties without deleting assessment history.
            cdp.call('Page.navigate',{'url':server.dashboard_url()})
            cdp.wait("!document.querySelector('#dashboard').hidden")
            click('[data-tab=students]')
            click('#configOpen')
            click('input[name=mode][value=individual]')
            submit('#wizardForm')
            cdp.wait("document.querySelector('#rosterInput')")
            submit('#wizardForm')
            cdp.wait("document.querySelector('[data-order]')")
            click('#addArea')
            cdp.evaluate("document.querySelectorAll('[data-name]')[1].value='操场清洁区';document.querySelectorAll('[data-count]')[1].value='1';document.querySelectorAll('[data-order]')[1].value='06、05、04、03、02、01'")
            check_no_overflow()
            cdp.screenshot(output/'campus-individual-areas.png')
            submit('#wizardForm')
            cdp.wait("document.querySelector('#stepTitle').textContent==='核对保存'")
            submit('#wizardForm')
            cdp.wait("document.querySelector('#confirmDialog').open")
            assert '剩余处罚' in cdp.evaluate("document.querySelector('#confirmText').textContent")
            click('#confirmYes')
            cdp.wait("document.querySelector('#wizard').hidden")
            tomorrow=service.view()['tomorrow_duty']['areas']
            assert [len(a['student_ids']) for a in tomorrow]==[2,1]
            assert all(a['mode']=='individual' for a in tomorrow)
            assert len(service.report()['details'])==2, 'configuration resolution deleted assessment history'
            # An actual non-localhost HTTP origin matches phone access: randomUUID
            # is unavailable there; getRandomValues still supports all writes.
            if server.addresses:
                address=next((a for a in server.addresses if a.startswith('192.168.')),server.addresses[0])
                cdp.call('Page.navigate',{'url':f'http://{address}:{server.server_address[1]}/'})
                cdp.wait("!document.querySelector('#loginScreen').hidden")
                field('#loginPassword','campus-password')
                submit('#loginForm')
                cdp.wait("!document.querySelector('#dashboard').hidden")
                assert cdp.evaluate('window.isSecureContext') is False
                click('[data-open=mark]')
                field('#markPenalty','other')
                cdp.evaluate("document.querySelector('#markPenalty').dispatchEvent(new Event('change'))")
                field('#markNote','局域网手机访问验证')
                submit('#markForm')
                cdp.wait("!document.querySelector('#markDialog').open")
                assert len(service.report()['details'])==3
            assert not cdp.errors,cdp.errors
            result={'result':'passed','desktop':'1440x1100','mobile':'390x844','javascript_errors':0,
                    'checks':['group initialization','multi-area weekly policy','overlap confirmation','group/person assessment',
                              'future swap and cancellation','attendance return','reminder settings with simulated startup',
                              'config preview/history','calendar day dialog','weekly assessment','config import/export',
                              'mobile dialogs','login/logout','separate read-only display','display snooze',
                              'individual mode configuration','penalty conflict preserves assessment','LAN insecure-context edits',
                              'sticky primary assessment action','wallpaper opt-in settings']}
            (output/'campus-browser-metrics.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result))
        finally:
            if cdp: cdp.ws.close()
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__': main()
