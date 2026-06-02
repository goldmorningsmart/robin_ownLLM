import json
import logging
import re
from pathlib import Path
from typing import cast

import os
import httpx
import aiofiles
import choix
import pandas as pd
from aviary.core import Message
from lmi import LiteLLMModel

from .configuration import RobinConfiguration
from .utils import (
    call_platform,
    format_assay_ideas,
    output_to_string,
    processing_ranking_output,
    run_comparisons,
    save_crow_files,
    uniformly_random_pairs,
)

logger = logging.getLogger(__name__)


async def experimental_assay(configuration: RobinConfiguration) -> str | None:

    logger.info("Starting selection of a relevant experimental assay.")
    logger.info("————————————————————————————————————————————————————")

    # Step 1: Generating queries for Crow

    logger.info("\nStep 1: Formulating relevant queries for literature search...")

    assay_literature_system_message = (
        configuration.prompts.assay_literature_system_message.format(
            num_assays=configuration.num_assays
        )
    )

    assay_literature_user_message = (
        configuration.prompts.assay_literature_user_message.format(
            num_queries=configuration.num_queries,
            disease_name=configuration.disease_name,
        )
    )

    assay_literature_query_messages = [
        Message(role="system", content=assay_literature_system_message),
        Message(role="user", content=assay_literature_user_message),
    ]

    assay_literature_query_result = await configuration.llm_client.call_single(
        assay_literature_query_messages
    )

    assay_literature_query_result_text = cast(str, assay_literature_query_result.text)
    assay_literature_queries = assay_literature_query_result_text.split("<>")
    logger.info("Generated Queries:")
    for ia, aquery in enumerate(assay_literature_queries):
        logger.info(f"{ia + 1}. {aquery}")

    experimental_assay_queries_dict = {}

    experimental_assay_queries_dict = {q: q for q in assay_literature_queries}

    # ### Step 2: Literature review on cell culture assays
'''
    logger.info("\nStep 2: Conducting literature search with Edison platform...")

    assay_lit_review = await call_platform(
        queries=experimental_assay_queries_dict,
        fh_client=configuration.edison_client,
        job_name=configuration.agent_settings.assay_lit_search_agent,
    )

    assay_lit_review_results = assay_lit_review["results"]
'''
    logger.info("\nStep 2: Conducting literature search with DeepSeek Platform...")

    # 1. 准备 DeepSeek 的配置（建议从环境变量或 config 中读取）
    DEEPSEEK_API_KEY = os.getenv("OPENAI_API_KEY", "从.env文件配置")  # 确保在 .env 文件中正确设置了 OPENAI_API_KEY
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"  # 或者是你本地/内网部署的网关地址

    assay_lit_review_results = []

    # 2. 使用 httpx 异步客户端并行或串行请求 DeepSeek
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 遍历你的 queries 字典（假设结构为 { "query_1": "...", "query_2": "..." }）
        for query_key, query_text in experimental_assay_queries_dict.items():
            logger.info(f"Processing {query_key} with DeepSeek...")
            
            system_prompt = (
                "You are an expert scientific research assistant specializing in biomedical sciences "
                "and experimental assay design. Provide a comprehensive, structured literature review "
                "and protocol synthesis based on the provided keywords. Focus on cell models, "
                "induction conditions, and functional readouts."
            )
            
            try:
                response = await client.post(
                    f"{DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "deepseek-chat",  # 或者是 deepseek-reasoner (R1推理模型，强烈推荐用于科研)
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Please review literature and propose assay setups for: {query_text}"}
                        ],
                        "temperature": 0.2
                    }
                )
                response.raise_for_status()
                result_json = response.json()
                
                # 提取 DeepSeek 返回的文本内容
                content = result_json["choices"][0]["message"]["content"]
                
                # 保持原有的数据格式结构，以防后续 save_crow_files 报错
                # 原版结构中每个 query 对应其生成的文献综述内容
                assay_lit_review_results.append({
                    "query": query_text,
                    "review": content
                })
                
            except Exception as e:
                logger.error(f"Failed to get response from DeepSeek for {query_key}: {e}")
                # 放入空数据防止完全中断
                assay_lit_review_results.append({f"{query_key}": f"Error generating: {str(e)}"})
    save_crow_files(
        assay_lit_review_results,
        run_dir=f"robin_output/{configuration.run_folder_name}/experimental_assay_literature_reviews",
        prefix="query",
    )

    assay_lit_review_output = output_to_string(assay_lit_review_results)

    # ### Step 3: Proposing cell culture assays

    logger.info("\nStep 3: Generating ideas for relevant experimental assays...")

    assay_proposal_system_message = (
        configuration.prompts.assay_proposal_system_message.format(
            num_assays=configuration.num_assays
        )
    )

    assay_proposal_user_message = (
        configuration.prompts.assay_proposal_user_message.format(
            num_assays=configuration.num_assays,
            disease_name=configuration.disease_name,
            assay_lit_review_output=assay_lit_review_output,
        )
    )

    assay_proposal_messages = [
        Message(role="system", content=assay_proposal_system_message),
        Message(role="user", content=assay_proposal_user_message),
    ]

    experimental_assay_ideas = await configuration.llm_client.call_single(
        assay_proposal_messages
    )

    response_text = cast(str, experimental_assay_ideas.text)
    if not response_text.strip():
        raise ValueError(
            "LLM returned an empty response during assay proposal generation."
        )
    try:
        assay_idea_json = json.loads(response_text)
    except json.JSONDecodeError as err:
        match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if not match:
            raise ValueError(
                f"LLM response did not contain a JSON array. Response: {response_text[:200]}"
            ) from err
        assay_idea_json = json.loads(match.group())
    assay_idea_list = format_assay_ideas(assay_idea_json)

    for assay_idea in assay_idea_list:
        logger.info(f"{assay_idea[:100]}...")

    assay_list_export_file = (
        f"robin_output/{configuration.run_folder_name}/experimental_assay_summary.txt"
    )

    async with aiofiles.open(assay_list_export_file, "w") as f:
        for i, item in enumerate(assay_idea_list):
            parts = item.split("<|>")
            strategy = parts[0]
            reasoning = parts[1]

            await f.write(f"Assay Candidate {i + 1}:\n")
            await f.write(f"{strategy}\n")
            await f.write(f"{reasoning}\n\n")

    logger.info(f"Successfully exported to {assay_list_export_file}")

    # ==========================================================================
    # ### Step 4: 替换原 call_platform 逻辑，使用 DeepSeek 生成报告
    # ==========================================================================
    logger.info("\nStep 4: Detailed investigation and evaluation via DeepSeek...")

    import httpx
    import os

    # 从环境或配置中读取 DeepSeek 的凭证与基础路径
    DEEPSEEK_API_KEY = os.getenv("OPENAI_API_KEY", "从.env文件配置")  # 确保在 .env 文件中正确设置了 OPENAI_API_KEY
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

    # 用来存放最终和原框架格式对齐的结果列表
    deepseek_results = []

    # 推荐对于这种需要“详细科学评估和假设生成（Hypothesis Generation）”的复杂学术任务
    # 使用带思维链的 deepseek-reasoner (R1) 模型。如果追求速度，可换回 deepseek-chat。
    MODEL_NAME = "deepseek-reasoner" 

    async with httpx.AsyncClient(timeout=300.0) as client:
        for assay_name, full_prompt_text in assay_hypothesis_queries.items():
            logger.info(f"Generating detailed hypothesis report for assay: '{assay_name}' with DeepSeek...")
            
            try:
                # 注意：原版 robin 脚本里是直接拼接了系统提示词、实验想法和格式要求。
                # 在调用 ChatCompletion 时，我们直接作为 user 消息整体投喂给模型。
                response = await client.post(
                    f"{DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": MODEL_NAME,
                        "messages": [
                            {
                                "role": "user", 
                                "content": full_prompt_text
                            }
                        ],
                        "temperature": 0.3  # 报告生成建议保持较低随机性
                    }
                )
                response.raise_for_status()
                response_json = response.json()
                
                # 提取模型生成的假说与评估报告文本
                generated_report = response_json["choices"][0]["message"]["content"]
                
                # 依照原 robin 内部的数据格式封装，让 save_crow_files 能够正确解析并写入本地
                # 这里的格式需要和下游的 has_hypothesis=True 处理逻辑对齐
                deepseek_results.append({
                    "assay_name": assay_name,
                    "hypothesis": generated_report
                })
                
            except Exception as e:
                logger.error(f"DeepSeek failed to generate report for '{assay_name}': {e}")
                deepseek_results.append({
                    "assay_name": assay_name,
                    "hypothesis": f"Error generating report via DeepSeek: {str(e)}"
                })

    # 将格式重新包裹回原有框架所预期的 {"results": [...]} 结构
    assay_hypotheses = {"results": deepseek_results}

    # ==========================================================================
    # ### 保持原有的保存逻辑不变
    # ==========================================================================
    save_crow_files(
        assay_hypotheses["results"],
        run_dir=f"robin_output/{configuration.run_folder_name}/experimental_assay_detailed_hypotheses",
        prefix="assay_hypothesis",
        has_hypothesis=True,
    )

    # ### Step 5: Selecting the top experimental assay

    logger.info("\nStep 5: Selecting the top experimental assay...")

    assay_hypothesis_df = pd.DataFrame(assay_hypotheses["results"])
    assay_hypothesis_df["index"] = assay_hypothesis_df.index

    assay_ranking_system_prompt = (
        configuration.prompts.assay_ranking_system_prompt.format(
            disease_name=configuration.disease_name
        )
    )

    assay_ranking_prompt_format = configuration.prompts.assay_ranking_prompt_format

    assay_ranking_output_folder = f"robin_output/{configuration.run_folder_name}"
    assay_ranking_output_folder_path = Path(assay_ranking_output_folder)
    assay_ranking_output_filepath = (
        assay_ranking_output_folder_path / "experimental_assay_ranking_results.csv"
    )
    assay_ranking_output_folder_path.mkdir(parents=True, exist_ok=True)

    assay_pairs_list = uniformly_random_pairs(n_hypotheses=configuration.num_assays)

    await run_comparisons(
        pairs_list=assay_pairs_list,
        client=configuration.llm_client,
        system_prompt=assay_ranking_system_prompt,
        ranking_prompt_format=assay_ranking_prompt_format,
        assay_hypothesis_df=assay_hypothesis_df,
        output_filepath=str(assay_ranking_output_filepath),
    )

    assay_ranking_df = processing_ranking_output(str(assay_ranking_output_filepath))
    games_data = assay_ranking_df["Game Score"].to_list()
    params = choix.ilsr_pairwise(configuration.num_assays, games_data, alpha=0.1)

    assay_ranked_results = pd.DataFrame()
    assay_ranked_results["hypothesis"] = assay_hypothesis_df["hypothesis"]
    assay_ranked_results["answer"] = assay_hypothesis_df["answer"]
    assay_ranked_results["strength_score"] = params
    assay_ranked_results["index"] = assay_hypothesis_df["index"]
    assay_ranked_results_sorted = assay_ranked_results.sort_values(
        by="strength_score", ascending=False
    )

    top_experimental_assay = assay_ranked_results_sorted["hypothesis"].iloc[0]

    logger.info(f"Experimental Assay Selected: {top_experimental_assay}")

    # ## Synthesizing goal for candidate generation using specified assay and disease

    async def synthesize_candidate_goal(
        assay_name: str, client: LiteLLMModel
    ) -> str | None:

        synthesize_user_content = configuration.prompts.synthesize_user_content.format(
            assay_name=assay_name, disease_name=configuration.disease_name
        )

        synthesize_system_message_content = (
            configuration.prompts.synthesize_system_message_content.format(
                disease_name=configuration.disease_name
            )
        )

        messages = [
            Message(role="system", content=synthesize_system_message_content),
            Message(role="user", content=synthesize_user_content),
        ]

        response = await client.call_single(messages)
        return cast(str, response.text)

    candidate_generation_goal = await synthesize_candidate_goal(
        top_experimental_assay, configuration.llm_client
    )

    logger.info(f"Candidate Generation Goal: {candidate_generation_goal}")

    return candidate_generation_goal
