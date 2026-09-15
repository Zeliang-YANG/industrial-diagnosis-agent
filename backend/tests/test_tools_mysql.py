"""使用自动清理的独立 MySQL 测试库，不读写原业务表。"""
import sys
from pathlib import Path
import unittest
from datetime import datetime, date
from uuid import uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import OperationalError
import db_models as db
from config import LINE_LAYOUT, SIM_CONFIG
from agent_tools import (query_kpi, query_device_state, query_fault_events,
                         query_telemetry_summary, query_line_timeline, query_line_kpi,
                         dispatch_tool, TOOL_SCHEMAS)
from knowledge_base import search_knowledge
from kpi_engine import calculate_daily_kpi, calculate_line_kpi
from app import app
from event_lifecycle import reconcile_stale_open_events, correct_known_interrupted_events

EID = LINE_LAYOUT[1]["cnc"]


class MySQLToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_bind = db.SessionLocal.kw['bind']
        cls.admin = create_engine(db.engine.url.set(database='mysql'))
        cls.dbname = 'industrial_agent_test_' + uuid4().hex[:12]
        with cls.admin.begin() as c:
            c.execute(text(f'CREATE DATABASE `{cls.dbname}`'))
        cls.test_engine = create_engine(db.engine.url.set(database=cls.dbname))
        db.Base.metadata.create_all(cls.test_engine)
        db.SessionLocal.configure(bind=cls.test_engine)
        with db.SessionLocal() as session:
            def state(eid, code, start, end, alarm=0):
                session.add(db.StatusEventLog(equip_id=eid, state_code=code,
                    start_time=datetime.fromisoformat(start), end_time=datetime.fromisoformat(end),
                    duration_sec=999999, alarm_code=alarm))
            def telemetry(day, clock, count):
                session.add(db.RawTelemetry(equip_id=EID, timestamp=datetime.fromisoformat(day+'T'+clock),
                    part_count=count, bad_count=0, machine_state='srun', spindle_speed=1000+count,
                    spindle_load=50, temperature=40))
            state(EID,'srun','2026-01-02T00:00','2026-01-02T01:00')
            state(EID,'su_down','2026-01-02T01:00','2026-01-02T01:43',101)
            state(EID,'srun','2026-01-02T01:43','2026-01-02T02:00')
            for role in ('robot','plc'):
                state(LINE_LAYOUT[1][role], 'swork' if role=='robot' else 'srun',
                      '2026-01-02T00:00','2026-01-02T02:00')
            telemetry('2026-01-02','00:00',0)
            telemetry('2026-01-02','02:00',660)
            state(EID,'su_down','2026-01-03T23:50','2026-01-04T00:20',107)
            state(EID,'srun','2026-01-05T00:00','2026-01-05T00:10')
            for clock,count in [('00:00',100),('00:01',105),('00:02',0),('00:03',4)]:
                telemetry('2026-01-05',clock,count)
            session.commit()

    @classmethod
    def tearDownClass(cls):
        db.SessionLocal.configure(bind=cls.original_bind)
        cls.test_engine.dispose()
        with cls.admin.begin() as c:
            c.execute(text(f'DROP DATABASE `{cls.dbname}`'))
        cls.admin.dispose()

    def test_43_minute_fault_and_metrics(self):
        result=query_kpi(EID,'2026-01-02')['current']
        self.assertEqual(result['stats']['t_down'],2580)
        self.assertEqual(result['metrics']['t_total'],7200)
        self.assertAlmostEqual(result['metrics']['availability'],4620/7200*100)
        self.assertAlmostEqual(result['metrics']['performance'],100)
        self.assertAlmostEqual(result['metrics']['oee'],4620/7200*100)
        fault=query_fault_events(EID,'2026-01-02')['events'][0]
        self.assertEqual(fault['duration_in_window_sec'],2580)
        self.assertFalse(fault['root_cause_confirmed'])

    def test_cross_midnight_clipping(self):
        result=query_kpi(EID,'2026-01-04')['current']
        self.assertEqual(result['stats']['t_down'],1200)
        self.assertEqual(query_fault_events(EID,'2026-01-04')['events'][0]['duration_in_window_sec'],1200)
        response=app.test_client().get('/api/events?date=2026-01-04')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['events'][0]['duration_sec'],1200)

    def test_counter_reset(self):
        result=query_kpi(EID,'2026-01-05')['current']
        self.assertEqual(result['total_qty'],9)
        self.assertIn('counter_reset_detected',result['warnings'])

    def test_telemetry_gap_is_explicit(self):
        result=query_kpi(EID,'2026-01-02')['current']
        self.assertIn('telemetry_gaps_detected',result['warnings'])
        self.assertEqual(result['data_quality'],'warning')
        self.assertEqual(result['evidence']['observed_segment_count'],2)
        self.assertEqual(result['evidence']['telemetry_gap_count'],1)
        self.assertEqual(result['evidence']['max_telemetry_gap_sec'],7200)
        self.assertEqual(result['evidence']['observed_coverage_sec'],0)

    def test_telemetry_summary_is_bounded_and_traceable(self):
        result=query_telemetry_summary(EID,'2026-01-02',start_time='00:00',end_time='02:01')
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['sample_count'],2)
        self.assertEqual(result['counters']['part_count_delta'],660)
        self.assertEqual(result['signals']['spindle_load']['avg'],50)
        self.assertEqual(result['telemetry_gap_count'],1)
        self.assertEqual(result['state_definitions']['srun'],'加工或设备运行')
        self.assertTrue(result['evidence']['first_ref'].startswith('raw_telemetry:'))
        self.assertEqual(dispatch_tool('query_telemetry_summary',{
            'equip_id':EID,'date':'2026-01-02','start_time':'25:00'
        })['code'],'invalid_arguments')

    def test_line_tools_expose_station_contributions_and_timeline(self):
        line=query_line_kpi(1,'2026-01-02')
        self.assertEqual(line['status'],'ok')
        self.assertEqual(len(line['current']['dashboard']['stations']),3)
        timeline=query_line_timeline(1,'2026-01-02',limit=20)
        self.assertEqual(timeline['status'],'ok')
        self.assertEqual(set(x['device_role'] for x in timeline['events']),{'cnc','robot','plc'})
        self.assertTrue(all(x['evidence_ref'].startswith('status_event_log:') for x in timeline['events']))
        self.assertEqual(timeline['page_window']['start'],'2026-01-02T00:00:00')
        self.assertEqual(timeline['page_window']['end'],'2026-01-02T02:00:00')

    def test_stale_open_event_overlaps_later_event(self):
        with db.SessionLocal() as session:
            session.add_all([
                db.StatusEventLog(equip_id=EID, state_code='ssby', start_time=datetime(2026,1,8,1)),
                db.StatusEventLog(equip_id=EID, state_code='srun', start_time=datetime(2026,1,8,2), end_time=datetime(2026,1,8,3)),
            ])
            session.commit()
        result=query_kpi(EID,'2026-01-08')['current']
        self.assertEqual(result['data_quality'],'invalid')
        self.assertIn('overlapping_state_events',result['warnings'])

    def test_reconcile_stale_open_events(self):
        with db.SessionLocal() as session:
            stale = db.StatusEventLog(
                equip_id=EID, state_code='ssby', start_time=datetime(2026,1,9,1)
            )
            later = db.StatusEventLog(
                equip_id=EID, state_code='srun', start_time=datetime(2026,1,9,2),
                end_time=datetime(2026,1,9,3), duration_sec=3600,
            )
            session.add_all([stale, later])
            session.add(db.RawTelemetry(
                equip_id=EID, timestamp=datetime(2026,1,9,1,10), part_count=1, bad_count=0
            ))
            session.commit()
            stale_id, later_id = stale.id, later.id
            changes = reconcile_stale_open_events(session, datetime(2026,1,9,4))
            session.refresh(stale)
            self.assertEqual(stale.end_time, datetime(2026,1,9,1,10))
            self.assertEqual(stale.duration_sec, 600)
            match = next(x for x in changes if x['event_id'] == stale_id)
            self.assertTrue(match['basis'].startswith('last_observation_before_gap:'))

            stale.end_time = later.start_time
            stale.duration_sec = 3600
            session.commit()
            corrected = correct_known_interrupted_events(session, [stale_id])
            session.refresh(stale)
            self.assertEqual(stale.end_time, datetime(2026,1,9,1,10))
            self.assertEqual(len(corrected), 1)

    def test_dynamic_cycle_is_shared(self):
        with patch.dict(SIM_CONFIG, {'ideal_cycle_sec': 14}):
            payload=query_kpi(EID,'2026-01-02')
            self.assertEqual(payload['definition']['ideal_cycle_sec'],14)
            self.assertEqual(payload['current']['metrics']['performance'],200)
            self.assertIn('performance_exceeds_100',payload['current']['warnings'])
            response=app.test_client().get('/api/kpi/daily?date=2026-01-02')
            self.assertEqual(response.json['performance'],200)

    def test_nonproduction_device_is_marked(self):
        result=query_kpi(LINE_LAYOUT[1]['robot'],'2026-01-02')['current']
        self.assertFalse(result['oee_applicable'])
        self.assertIn('insufficient_counter_samples',result['warnings'])

    def test_missing_and_validation(self):
        self.assertEqual(query_kpi(EID,'2026-01-06')['status'],'no_data')
        compared=query_kpi(EID,'2026-01-02',compare_date='2026-01-06')
        self.assertIsNone(compared['comparison']['delta_percentage_points'])
        for args in [dict(equip_id="';DROP TABLE x;--",date='2026-01-02'),
                     dict(equip_id=EID,date='2026-02-30'),
                     dict(equip_id=EID,date='2026-01-02',sql='DELETE')]:
            self.assertEqual(dispatch_tool('query_kpi',args)['code'],'invalid_arguments')
        self.assertEqual(dispatch_tool('query_sql',{})['code'],'unknown_tool')

    def test_pagination(self):
        first=query_device_state(EID,'2026-01-02',limit=2)
        self.assertTrue(first['truncated'])
        second=query_device_state(EID,'2026-01-02',limit=2,after_id=first['next_after_id'])
        self.assertFalse(second['truncated'])
        self.assertEqual(len(second['events']),1)
        self.assertTrue(set(x['id'] for x in first['events']).isdisjoint(x['id'] for x in second['events']))

    def test_tools_are_read_only(self):
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany):
            statements.append(statement.strip().split()[0].upper())
        event.listen(self.test_engine,'before_cursor_execute',capture)
        try:
            query_kpi(EID,'2026-01-02')
            query_device_state(EID,'2026-01-02')
            query_fault_events(EID,'2026-01-02')
            query_telemetry_summary(EID,'2026-01-02')
            query_line_timeline(1,'2026-01-02')
            query_line_kpi(1,'2026-01-02')
        finally:
            event.remove(self.test_engine,'before_cursor_execute',capture)
        self.assertTrue(statements)
        self.assertEqual(set(statements),{'SELECT'})

    def test_industrial_knowledge_retrieval(self):
        result=search_knowledge('CNC OEE下降应该怎么排查？', top_k=3)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['matches'][0]['id'],'KB-OEE-001#2')
        self.assertIn('知识库 KB-OEE-001',result['matches'][0]['citation'])
        self.assertEqual(result['matches'][0]['authority'],'project-authored-runbook')
        self.assertEqual(result['matches'][0]['reviewed_at'],'2026-09-08')
        self.assertEqual(result['knowledge_base']['document_count'],6)
        self.assertGreaterEqual(result['knowledge_base']['chunk_count'],20)
        self.assertIn('不是设备厂商维修手册',result['limitations'])
        schema=next(item for item in TOOL_SCHEMAS if item['name']=='search_knowledge')
        self.assertEqual(schema['parameters']['required'],['query'])
        self.assertNotIn('equip_id',schema['parameters']['properties'])

    def test_knowledge_validation_and_no_match(self):
        self.assertEqual(dispatch_tool('search_knowledge',{'query':''})['code'],'invalid_arguments')
        self.assertEqual(search_knowledge('火星天气量子香蕉')['status'],'no_match')

    def test_database_failure_is_not_no_data(self):
        with patch('agent_tools.calculate_daily_kpi',side_effect=OperationalError('select',{},Exception())):
            result=dispatch_tool('query_kpi',dict(equip_id=EID,date='2026-01-02'))
        self.assertEqual(result['code'],'database_unavailable')

    def test_http_and_line_parity(self):
        client=app.test_client()
        tool=client.post('/api/tools/query_kpi',json={'equip_id':EID,'date':'2026-01-02'})
        daily=client.get('/api/kpi/daily',query_string={'equip_id':EID,'date':'2026-01-02'})
        self.assertEqual(tool.status_code,200)
        self.assertEqual(tool.json['current']['metrics'],daily.json)
        workshop=client.get('/api/workshop/kpi?date=2026-01-02&persist=0')
        self.assertEqual(workshop.status_code,200)
        line=calculate_line_kpi(date(2026,1,2),persist=False,verbose=False)
        self.assertEqual(line['metrics']['oee'],workshop.json['workshop']['lines'][0]['metrics']['oee'])
        self.assertEqual(len(client.get('/api/tools').json['tools']),7)
        self.assertEqual(client.post('/api/tools/query_kpi',json={'date':'bad'}).status_code,400)


if __name__=='__main__':
    unittest.main(verbosity=2)
