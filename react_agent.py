from langchain.agents import create_agent
from model.factory import chat_model_factory
from utils.prompt_loader import load_system_prompts
from tools.agent_tools import (rag_summarize,get_weather,get_user_id,
get_user_location,get_current_month,fetch_external_data,fill_context_for_report)

from tools.middleware import monitor_tool,log_before_model,report_prompt_switch



class ReactAgent:
    def __init__(self):
        self.agent=create_agent(
            model=chat_model_factory.generator(),
            system_prompt=load_system_prompts(),
            tools=[rag_summarize,get_weather,get_user_id,get_user_location,
                   fill_context_for_report,get_current_month,fetch_external_data],
            middleware=[monitor_tool,log_before_model,report_prompt_switch]
        )


    def execute_stream(self,qurry:str):
        input_dict={
            "messages":[
                {
                    "role":"user",
                    "content":qurry,
                }
            ]
        }
        for chunk  in self.agent.stream(input_dict,stream_mode="values",context={"report":False}):
            latest_message=chunk["messages"][-1]
            if latest_message.content:
                yield latest_message.content.strip()+"\n"


if __name__=="__main__":
    agent=ReactAgent()
    for chunk in agent.execute_stream("给我生成我的使用报告"):
        print(chunk,end="",flush=True)


