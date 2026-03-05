# mermaid live editor에서 그래프 시각화하기 위한 스크립트입니다. 
# graph.py에서 정의한 main_graph의 구조를 mermaid 형식으로 출력합니다.

import os
from dotenv import load_dotenv

load_dotenv()

from src.workflow.main_graph.graph import main_graph

print(main_graph.get_graph().draw_mermaid())