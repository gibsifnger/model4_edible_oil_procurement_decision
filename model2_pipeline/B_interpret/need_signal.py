"""
B_interpret.need_signal
- need_buy_flag를 B 레이어에 명시적으로 두기 위한 공통 유틸
“여러 신호가 여기저기 있더라도,
최종적으로 B에서는 이 건이 구매 필요 건인지 아닌지를 한 번 더 통일해서 보겠다.”
"""
from __future__ import annotations
import pandas as pd

# “이 월/이 장면은 **사야 하는 상황으로 볼 만한가?**를 한 줄짜리 신호로 바꾼다.” 
def infer_need_buy_flag_from_context(row: pd.Series) -> int:
    for a_col, b_col in [
        # “필요 신호를 볼 때, 먼저 최종 정리된 신호를 보고,
        # 그게 없으면 기본 예측 신호, 그것도 없으면 룰 신호까지 내려가서 확인한다.”
        ("target_a_final_pred", "target_b_final_pred"),
        ("target_a_pred", "target_b_pred"),
        ("target_a_rule", "target_b_rule"),
    ]:
        a = int(row.get(a_col, 0) or 0)
        b = int(row.get(b_col, 0) or 0)
        # “A 쪽 수요든 B 쪽 수요든, 둘 중 하나라도 매입 필요 신호가 서 있으면
        # 회사 입장에서는 일단 구매 검토 건으로 올린다.”
        if a == 1 or b == 1:
            return 1
    return 0

# 이 모듈에서 외부로 내보낼 공식 함수가 infer_need_buy_flag_from_context 하나라는 뜻이다.
__all__ = ["infer_need_buy_flag_from_context"]
