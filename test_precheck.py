import traceback
from mes_ops.pre_check import PreCheckEngine

engine = PreCheckEngine()
try:
    result = engine.run_checks_for_request('test_request_id', 'system', True)
    print('Success!')
    print('Passed:', result.get('passed'))
except Exception as e:
    print('Error:', e)
    traceback.print_exc()
