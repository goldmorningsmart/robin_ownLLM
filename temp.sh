git clone https://github.com/Future-House/robin.git
cd robin
DEEPSEEK_API_KEY=sk-xxxxxx


sudo nano /etc/docker/daemon.json


{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://huecker.io",
    "https://dockerhub.timtech.cn",
    "https://noohub.ru"
  ]
}

sudo systemctl daemon-reload
sudo systemctl restart docker

docker build -t robin .
sudo docker run -p 8888:8888 --env-file .env robin

config = RobinConfiguration(
    disease_name="dry age-related macular degeneration",
    llm_name="openai/deepseek-reasoner",  
    llm_config={
        "api_base": "https://api.deepseek.com/v1",  # 告诉它别去 OpenAI，去 DeepSeek 的服务器
        "timeout": 300.0,      # 核心：将超时时间从 60 秒延长到 300 秒（5分钟）
    }
)
############
#test your api
############
import litellm
import os

# 2. 原生测试
try:
    response = litellm.completion(
        model="openai/deepseek-reasoner",
        messages=[{"content": "hi", "role": "user"}],
        api_base="https://api.deepseek.com/v1",
        timeout=10.0
    )
    print("✅ 成功连上 DeepSeek！回应内容:", response.choices[0].message.content)
except Exception as e:
    print("❌ 根本没连上，报错原因:", e)


            {
            "model_name": "o4-mini",
            "litellm_params": {
                "model": "o4-mini",
                "api_key": "",
                "timeout": 300,
            },
        },

docker exec -it [容器ID或名称] /bin/sh
docker start [容器ID或名称]
docker run -d --name [旧容器名称] -p 8080:80 [镜像名称]
docker cp [容器ID或容器名称]:[容器内文件的绝对路径] [宿主机的目标路径]

docker cp 75ab78bccb3f:/app/robin_output ./my_output
docker stop 75ab78bccb3f
sudo docker run -d --name 75ab78bccb3f -p 8888:8888 --env-file .env robin

sudo docker exec -it 75ab78bccb3f jupyter server password