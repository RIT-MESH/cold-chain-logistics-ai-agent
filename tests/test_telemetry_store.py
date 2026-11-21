from unittest.mock import MagicMock, patch
from src.telemetry.store import save_event
from src.telemetry.worker import process_batch
from tests.test_telemetry import BASE, StubSqs, StubSns

class FakeConnection:
    def __init__(self, existing=False):
        self.statements=[]
        self.existing=existing
    def execute(self, stmt, params):
        self.statements.append((str(stmt),params))
        result=MagicMock()
        result.first.return_value=None if self.existing else ("e1",)
        return result

class Transaction:
    def __init__(self,conn): self.conn=conn
    def __enter__(self): return self.conn
    def __exit__(self,*a): pass
class Engine:
    def __init__(self,existing=False): self.conn=FakeConnection(existing)
    def begin(self): return Transaction(self.conn)

def test_new_incident_persists_event_and_incident():
    engine=Engine()
    from src.telemetry.events import classify
    assert save_event(engine,BASE,classify(BASE)) is True
    assert len(engine.conn.statements)==2

def test_duplicate_event_does_not_recreate_incident():
    engine=Engine(existing=True)
    from src.telemetry.events import classify
    assert save_event(engine,BASE,classify(BASE)) is False
    assert len(engine.conn.statements)==1

def test_duplicate_does_not_publish():
    import json
    sqs,sns=StubSqs(BASE),StubSns()
    assert process_batch(sqs,sns,'queue','topic',engine=Engine(existing=True))==1
    assert not sns.sent
