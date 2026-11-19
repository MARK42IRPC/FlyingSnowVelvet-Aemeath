"""Independent contracts for real-engine hidden-baseline calibration."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import balance, roster
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.model import MatchPhase
from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.stats import DangoStats


class DangoBalanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dango-balance-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def stats(self,name):
        return DangoStats(self.root/f'{name}.json')

    def test_laboratory_entry_uses_the_real_fighters_without_exposing_baseline(self):
        public=tuple(roster.ROSTER)
        for side in ('left','right'):
            match=balance.create_benchmark_match(self.stats(side),character_id='xigelika',seed=17,side=side)
            self.assertEqual(match.phase,MatchPhase.FIGHT)
            self.assertGreater(match.countdown,0)
            self.assertEqual(match.series.format.format_id,'bo1')
            self.assertEqual(match.dango_by_side(side).character_id,'xigelika')
            other=match.dango_by_side('right' if side=='left' else 'left')
            self.assertEqual((other.character_id,other.max_hp,other.contact_damage_range),('benchmark',500,(1,5)))
            match.prepare_round()
            self.assertFalse(match.choose_dango('left','benchmark'))
        self.assertEqual(tuple(roster.ROSTER),public)
        self.assertNotIn('benchmark',{entry.character_id for entry in roster.ROSTER})
        for character,side in (('benchmark','left'),('missing','left'),('amis','middle')):
            with self.assertRaises(ValueError):
                balance.create_benchmark_match(self.stats('invalid'),character_id=character,seed=0,side=side)

    def test_real_series_replays_seeds_and_sides_and_preserves_separate_stats(self):
        first_stats=self.stats('first')
        before=first_stats.snapshot()['total_rounds']
        progress=[]
        first=balance.run_benchmark_series(first_stats,character_id='feixue',count=2,start_seed=31,hp=1,progress=progress.append)
        second=balance.run_benchmark_series(self.stats('second'),character_id='feixue',count=2,start_seed=31,hp=1)
        self.assertEqual(first,second)
        self.assertEqual([(row['seed'],row['side']) for row in first],[(31,'left'),(32,'right')])
        self.assertEqual(progress,[1,2])
        self.assertEqual(first_stats.snapshot()['total_rounds'],before+2)
        self.assertTrue(all(row['outcome']=='loss' and row['participant_hp']==0 and row['benchmark_hp']>0 for row in first))

    def test_probe_prefix_predicts_real_hp_for_both_random_skill_characters(self):
        for character in ('daniya','xigelika','feixue'):
            with self.subTest(character=character):
                probes=balance.run_benchmark_series(self.stats(character+'-probes'),character_id=character,
                                                    count=2,start_seed=73,thresholds=True)
                for index,probe in enumerate(probes):
                    for hp in (probe['received_damage']-1,probe['received_damage'],probe['received_damage']+1):
                        replay_start=probe['seed']-(index%2)
                        actual=balance.run_benchmark_series(self.stats(f'{character}-{index}-{hp}'),
                            character_id=character,count=2,start_seed=replay_start,hp=hp)[index]
                        self.assertEqual(actual['seed'],probe['seed'])
                        self.assertEqual(actual['side'],probe['side'])
                        self.assertEqual(actual['outcome'],balance.outcome_at_hp(probe,hp),(character,hp,probe,actual))

    def test_simultaneous_body_finish_uses_the_whole_draw_interval(self):
        body={'received_damage':3,'previous_received_damage':1,'body_finish':True}
        skill={**body,'body_finish':False}
        self.assertEqual([balance.outcome_at_hp(body,hp) for hp in (1,2,3,4)],['loss','draw','draw','win'])
        self.assertEqual([balance.outcome_at_hp(skill,hp) for hp in (1,2,3,4)],['loss','loss','loss','win'])
        # A deliberately skill-free fixture exercises simultaneous basic KO;
        # the production Feixue now has a timed slash and is not this fixture.
        plain=tuple(replace(e,frost_slash=None) if e.character_id=='feixue' else e for e in roster.ROSTER)
        with patch.object(roster,'ROSTER',plain):
            probes=balance.run_benchmark_series(self.stats('body-probes'),character_id='feixue',count=2,start_seed=43,thresholds=True)
            self.assertTrue(all(row['body_finish'] and row['contacts']==500 for row in probes))
            candidate=probes[0]['received_damage']
            real=balance.run_benchmark_series(self.stats('body-real'),character_id='feixue',count=2,start_seed=43,hp=candidate)
        self.assertEqual(real[0]['outcome'],'draw')
        self.assertEqual(real[0]['participant_hp'],0)
        self.assertEqual(real[0]['benchmark_hp'],0)

    def test_choose_hp_selects_smallest_tied_half_score_and_rejects_empty(self):
        rows=[{'received_damage':3,'previous_received_damage':1,'body_finish':True},
              {'received_damage':5,'previous_received_damage':4,'body_finish':False}]
        self.assertEqual(balance.choose_hp(rows),4)
        self.assertEqual(balance.choose_hp(rows,step=1),4)
        self.assertEqual(balance.choose_hp(rows,step=5),5)
        self.assertEqual(balance.choose_hp(rows,step=3),3)
        for step in (0,-1,.5,2.0):
            with self.subTest(step=step),self.assertRaises(ValueError):
                balance.choose_hp(rows,step=step)
        probes=[dict(row,outcome='win',duration_seconds=1,side=side)
                for row,side in zip(rows,('left','right'))]
        prediction=balance.summarize(probes,candidate_hp=4)
        self.assertEqual((prediction['win'],prediction['loss'],prediction['draw']),(1,1,0))
        self.assertEqual(prediction['balance_score'],.5)
        self.assertEqual(balance.choose_hp([{'received_damage':0,'previous_received_damage':0,'body_finish':False}]),1)
        with self.assertRaises(ValueError):
            balance.choose_hp([])
        with self.assertRaises(ValueError):
            balance.outcome_at_hp(rows[0],0)

    def test_summary_keeps_draw_score_separate_from_raw_win_rate(self):
        rows=[{'outcome':'draw','side':side,'duration_seconds':2} for side in ('left','right')]
        summary=balance.summarize(rows)
        self.assertEqual((summary['win'],summary['loss'],summary['draw']),(0,0,2))
        self.assertEqual((summary['win_rate'],summary['draw_rate'],summary['balance_score']),(0,1,.5))
        self.assertEqual(summary['score_95_normal'],[.5,.5])
        rows=[{'outcome':outcome,'side':'left' if i%2==0 else 'right','duration_seconds':2}
              for i,outcome in enumerate(('win','draw','draw','loss'))]
        summary=balance.summarize(rows)
        half=1.959963984540054*math.sqrt(1/24)
        self.assertAlmostEqual(summary['score_95_normal'][0],.5-half)
        self.assertAlmostEqual(summary['score_95_normal'][1],.5+half)
        with self.assertRaises(ValueError):
            balance.summarize([])

    def test_exact_theory_has_expected_contact_counts_and_near_half_scores(self):
        amis=balance.theoretical_score('amis',180)
        self.assertEqual((amis['contacts_to_defeat_baseline'],amis['body_finish']),(60,False))
        self.assertEqual(amis['draw_rate'],0)
        self.assertTrue(.475<amis['balance_score']<.5,amis)
        plain=tuple(replace(e,frost_slash=None) if e.character_id=='feixue' else e for e in roster.ROSTER)
        with patch.object(roster,'ROSTER',plain):
            feixue=balance.theoretical_score('feixue',1499)
        self.assertEqual((feixue['contacts_to_defeat_baseline'],feixue['body_finish']),(500,True))
        self.assertGreater(feixue['draw_rate'],0)
        self.assertAlmostEqual(feixue['balance_score'],.5,delta=.001)
        self.assertAlmostEqual(feixue['win_rate']+.5*feixue['draw_rate'],feixue['balance_score'])
        for character in ('daniya','xigelika','feixue','missing','benchmark'):
            with self.assertRaises(ValueError):
                balance.theoretical_score(character,100)

    def test_invalid_batches_and_unfinished_guard_never_award_a_result(self):
        for options in ({'count':0},{'count':3},{'count':2,'hp':0},
                        {'count':2,'hp':100,'thresholds':True},{'count':2,'max_sim_seconds':0}):
            with self.assertRaises(ValueError):
                balance.run_benchmark_series(self.stats('bad'),character_id='feixue',**options)
        stats=self.stats('guard')
        with self.assertRaisesRegex(RuntimeError,'Unfinished benchmark: feixue, seed=0'):
            balance.run_benchmark_series(stats,character_id='feixue',count=2,max_sim_seconds=.001)
        self.assertEqual(stats.snapshot()['total_rounds'],0)

    def test_cli_uses_temporary_data_and_reports_all_real_matches(self):
        workspace=Path(__file__).resolve().parents[1]
        output=self.root/'report.json'
        sentinel=self.root/'player-home-must-not-exist'
        env=dict(os.environ,PYTHONIOENCODING='utf-8',AEMEATH_DESK_PET_HOME=str(sentinel))
        completed=subprocess.run([sys.executable,str(workspace/'scripts/simulate_dango_balance.py'),
            '--character','feixue','--mode','verify','--hp','1','--count','2','--start-seed','41','--out',str(output)],
            cwd=workspace,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertFalse(sentinel.exists())
        report=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['summary']['count'],2)
        self.assertEqual(report['summary']['loss'],2)
        self.assertEqual(report['benchmark']['max_hp'],500)
        self.assertEqual(report['benchmark']['contact_damage_range'],[1,5])
        self.assertEqual([row['seed'] for row in report['matches']],[41,42])
        self.assertIn('balance.py',report['source_sha256'])

    def test_cli_thresholds_default_to_five_hp_grid(self):
        workspace=Path(__file__).resolve().parents[1]
        output=self.root/'threshold-report.json'
        sentinel=self.root/'threshold-player-home-must-not-exist'
        env=dict(os.environ,PYTHONIOENCODING='utf-8',AEMEATH_DESK_PET_HOME=str(sentinel))
        completed=subprocess.run([sys.executable,str(workspace/'scripts/simulate_dango_balance.py'),
            '--character','amis','--mode','thresholds','--count','2','--start-seed','41','--out',str(output)],
            cwd=workspace,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertFalse(sentinel.exists())
        report=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['summary']['count'],2)
        self.assertEqual(report['hp_step'],5)
        self.assertGreater(report['hp'],0)
        self.assertEqual(report['hp']%5,0)
        self.assertEqual([row['seed'] for row in report['matches']],[41,42])


if __name__=='__main__':
    unittest.main()
