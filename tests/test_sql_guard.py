import pytest
from src.sql_guard import validate_query

@pytest.mark.parametrize('q',[
 'SELECT TOP 10 shipment_id, cargo_type FROM FDE_VIEWS.VW_SHIPMENTS',
 'SELECT COUNT(*) AS count FROM FDE_VIEWS.VW_TELEMETRY',
 'SELECT TOP 5 humidity_pct FROM FDE_VIEWS.VW_TELEMETRY',
 # Phase 0 additions: aggregate with WHERE needs no TOP (returns one row)
 'SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY WHERE temperature_c > 4'])
def test_allow(q):assert validate_query(q)==q

@pytest.mark.parametrize('q',[
 'DELETE FROM FDE_VIEWS.VW_SHIPMENTS',
 'SELECT * FROM unified.shipments',
 'SELECT * FROM FDE_VIEWS.VW_TELEMETRY',
 'SELECT shipment_id FROM FDE_VIEWS.VW_SHIPMENTS; DROP TABLE unified.shipments',
 'SELECT a.shipment_id FROM FDE_VIEWS.VW_SHIPMENTS a JOIN dbo.secret b ON a.shipment_id=b.id',
 'SELECT shipment_id FROM FDE_VIEWS.VW_FLEET_HISTORY',
 # Phase 0 additions: procedure execution, TOP bounds, CTEs, wrong schema, UNION
 "EXEC xp_cmdshell 'dir'",
 'SELECT TOP 101 shipment_id FROM FDE_VIEWS.VW_SHIPMENTS',
 'SELECT TOP 0 shipment_id FROM FDE_VIEWS.VW_SHIPMENTS',
 'WITH cte AS (SELECT 1 AS x) SELECT x FROM cte',
 'SELECT TOP 5 shipment_id FROM dbo.VW_TELEMETRY',
 'SELECT TOP 5 shipment_id FROM FDE_VIEWS.VW_SHIPMENTS UNION SELECT TOP 5 shipment_id FROM FDE_VIEWS.VW_ALERTS'])
def test_block(q):
 with pytest.raises(ValueError):validate_query(q)