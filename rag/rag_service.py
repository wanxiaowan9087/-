#总结服务类，用户提问，搜索参考资料，将参考资料和提问交给模型，然后返回总结
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from model.factory import chat_model_factory
from utils.prompt_loader import load_rag_prompts
from rag.vector_store import VectorStoreService

def print_prompt(prompt:str)->str:
    print("="*20)
    print(prompt.to_string())
    print("="*20)
    return prompt



class RagSummarizeService(object):
    def __init__(self):
        self.vector_store=VectorStoreService()
        self.retriever=self.vector_store.get_retriever()
        self.prompt_text=load_rag_prompts()
        self.prompt_template=PromptTemplate.from_template(self.prompt_text)
        self.model=chat_model_factory.generator()
        self.chain=self._init_chian()


    def _init_chian(self):
        chain=self.prompt_template|print_prompt|self.model|StrOutputParser()
        return chain

    def retriever_docs(self,qurry:str)->list[Document]:
        return self.retriever.invoke(qurry)

    def  rag_summarize(self,qurry:str)->str:
        context_docs=self.retriever_docs(qurry)
        context=""
        counter=1
        for doc in context_docs:
            context+=f"检索到{counter}篇资料: 参考资料为：{doc.page_content}|参考元数据为：{doc.metadata}"
            counter+=1

        return self.chain.invoke(
            {
                "input":qurry,
                "context":context
            }
        )


if __name__=="__main__":
    rag = RagSummarizeService()
    print(rag.rag_summarize("大户型适合什么扫地机器人"))