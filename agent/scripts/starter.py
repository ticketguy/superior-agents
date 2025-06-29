"""
Updated Starter Script with Background Intelligence Monitor
Uses generic RPC provider naming - works with Helius, QuickNode, Alchemy, Custom RPCs
"""

import asyncio
import os
import requests
import inquirer
import time

from src.db import SQLiteDB
from src.client.rag import RAGClient
from tests.mock_client.rag import MockRAGClient
from tests.mock_client.interface import RAGInterface
from tests.mock_sensor.security import MockSecuritySensor
from src.sensor.security import SecuritySensor
from src.sensor.interface import SecuritySensorInterface
from src.db import DBInterface
from typing import Callable
from src.rpc_config import FlexibleRPCConfig
from src.agent.security import SecurityAgent, SecurityPromptGenerator
from src.datatypes import StrategyData
from src.container import ContainerManager
from src.helper import (
    services_to_envs,
    services_to_prompts,
)
from src.genner import get_genner
from src.genner.Base import Genner
from src.client.openrouter import OpenRouter
from src.summarizer import get_summarizer
from anthropic import Anthropic
import docker
from functools import partial
from src.flows.security import assisted_flow as security_assisted_flow
from loguru import logger
from src.constants import SERVICE_TO_ENV
from src.manager import fetch_default_prompt
from dotenv import load_dotenv

# NEW: Import Background Monitor
from src.intelligence.background_monitor import BackgroundIntelligenceMonitor, start_background_monitor

load_dotenv()

# Security agent default configuration
FE_DATA_SECURITY_DEFAULTS = {
    "agent_name": "",
    "type": "security",
    "model": "claude",
    "mode": "default",
    "role": "security analyst protecting Web3 wallets",
    "network": "solana",
    "time": "24h",
    "research_tools": ["Solana RPC", "Threat Intelligence"],
    "security_tools": ["quarantine", "block", "monitor", "analyze"],
    "metric_name": "security",
    "notifications": ["blockchain_alerts"],
}


async def start_security_agent_with_background_monitor(
    agent_type: str,
    session_id: str,
    agent_id: str,
    fe_data: dict,
    genner: Genner,
    rag: RAGInterface,
    sensor: SecuritySensorInterface,
    db: DBInterface,
    meta_swap_api_url: str,
    stream_fn: Callable[[str], None] = lambda x: print(x, flush=True, end=""),
):
    """Start security agent with AI code generation AND 24/7 background monitoring"""
    role = fe_data["role"]
    network = fe_data["network"]
    services_used = fe_data["research_tools"]
    security_tools = fe_data["security_tools"]
    metric_name = fe_data["metric_name"]
    notif_sources = fe_data["notifications"]
    time_ = fe_data["time"]

    in_con_env = services_to_envs(services_used)
    apis = services_to_prompts(services_used)
    if fe_data["model"] == "deepseek":
        fe_data["model"] = "deepseek_or"

    prompt_generator = SecurityPromptGenerator(prompts=fe_data["prompts"])

    container_manager = ContainerManager(
        docker.from_env(),
        "agent-executor",
        "./code",
        in_con_env=in_con_env,
    )

    summarizer = get_summarizer(genner)
    previous_strategies = db.fetch_all_strategies(agent_id)

    rag.save_result_batch_v4(previous_strategies)

    # Create SecurityAgent with AI code generation
    agent = SecurityAgent(
        agent_id=agent_id,
        sensor=sensor,
        genner=genner,
        container_manager=container_manager,
        prompt_generator=prompt_generator,
        db=db,
        rag=rag,
    )

    # Connect SecuritySensor to SecurityAgent for real-time quarantine decisions
    if hasattr(sensor, 'set_security_agent'):
        sensor.set_security_agent(agent)
        logger.info("🔗 Connected SecuritySensor to SecurityAgent for AI analysis")
    
    # 🚀 NEW: Start Background Intelligence Monitor
    logger.info("🔍 Starting 24/7 Background Intelligence Monitor...")
    try:
        background_monitor = await start_background_monitor(db, rag)
        logger.info("✅ Background Intelligence Monitor started successfully!")
        logger.info("📡 Now monitoring: Twitter, Reddit, blacklisted wallets, blockchain patterns")
    except Exception as e:
        logger.warning(f"⚠️ Background Monitor failed to start: {e}")
        logger.info("📊 Continuing without background monitoring")
        background_monitor = None
    
    # Start real-time incoming transaction monitoring
    if hasattr(sensor, 'start_incoming_monitor'):
        try:
            await sensor.start_incoming_monitor()
            logger.info("🛡️ Real-time transaction monitoring started!")
        except Exception as e:
            logger.warning(f"⚠️ Could not start real-time monitoring: {e}")
            logger.info("📊 Continuing with periodic analysis only")

    flow_func = partial(
        security_assisted_flow,
        agent=agent,
        session_id=session_id,
        role=role,
        network=network,
        time=time_,
        apis=apis,
        security_tools=security_tools,
        metric_name=metric_name,
        meta_swap_api_url=meta_swap_api_url,
        summarizer=summarizer,
    )

    # Run main cycle with background monitoring active
    try:
        await run_cycle_with_background_monitor(
            agent,
            notif_sources,
            flow_func,
            db,
            session_id,
            agent_id,
            fe_data,
            background_monitor,
        )
    finally:
        # Cleanup: Stop background monitor when main cycle ends
        if background_monitor:
            logger.info("🛑 Stopping background monitor...")
            await background_monitor.stop_monitoring()


async def run_cycle_with_background_monitor(
    agent: SecurityAgent,
    notif_sources: list[str],
    flow: Callable[[StrategyData | None, str | None], None],
    db: DBInterface,
    session_id: str,
    agent_id: str,
    fe_data: dict | None = None,
    background_monitor: BackgroundIntelligenceMonitor | None = None,
):
    """Execute security agent workflow cycle with background intelligence"""
    cycle_count = 0
    
    while True:  # Continuous monitoring loop
        try:
            cycle_count += 1
            logger.info(f"🔄 Starting security cycle #{cycle_count}")
            
            # Get previous strategy context
            prev_strat = agent.db.fetch_latest_strategy(agent.agent_id)
            if prev_strat is not None:
                logger.info(f"📚 Using previous security strategy: {prev_strat.summarized_desc[:100]}...")
                agent.rag.save_result_batch_v4([prev_strat])

            # Get latest notifications + background intelligence
            notif_limit = 5 if fe_data is None else 2
            current_notif = agent.db.fetch_latest_notification_str_v2(
                notif_sources, notif_limit
            )
            
            # 🚀 NEW: Enhance notifications with background intelligence
            if background_monitor:
                try:
                    # Get recent threat intelligence from background monitor
                    monitor_status = await background_monitor.get_monitoring_status()
                    
                    if monitor_status['statistics']['threats_discovered'] > 0:
                        threat_summary = f"Background Monitor Alert: {monitor_status['statistics']['threats_discovered']} new threats detected. "
                        threat_summary += f"Tracking {monitor_status['blacklisted_wallets']} blacklisted wallets. "
                        threat_summary += f"Last update: {monitor_status['statistics']['last_update']}"
                        
                        # Combine with existing notifications
                        if current_notif:
                            current_notif = f"{threat_summary}\n\nOther notifications: {current_notif}"
                        else:
                            current_notif = threat_summary
                        
                        logger.info(f"🚨 Enhanced notifications with background intelligence")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to get background intelligence: {e}")
            
            logger.info(f"📢 Processing notifications: {current_notif[:100] if current_notif else 'No new notifications'}...")

            # Run the main security analysis flow
            flow(prev_strat=prev_strat, notif_str=current_notif)
            db.add_cycle_count(session_id, agent_id)
            
            # Show monitoring status every 10 cycles
            if cycle_count % 10 == 0 and background_monitor:
                try:
                    status = await background_monitor.get_monitoring_status()
                    logger.info(f"📊 Background Monitor Status:")
                    logger.info(f"   🔍 Threats discovered: {status['statistics']['threats_discovered']}")
                    logger.info(f"   🚫 Wallets tracked: {status['blacklisted_wallets']}")
                    logger.info(f"   📱 Social media scans: {status['statistics']['social_media_scans']}")
                    logger.info(f"   💾 Database updates: {status['statistics']['database_updates']}")
                except Exception as e:
                    logger.warning(f"⚠️ Error getting monitor status: {e}")
            
            # Wait before next cycle (configurable)
            cycle_interval = int(os.getenv('SECURITY_CYCLE_INTERVAL', 900))  # 15 minutes default
            logger.info(f"⏰ Waiting {cycle_interval} seconds before next cycle...")
            await asyncio.sleep(cycle_interval)
            
        except KeyboardInterrupt:
            logger.info("🛑 Security monitoring stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Error in security cycle: {e}")
            # Wait before retrying
            await asyncio.sleep(60)


def setup_security_sensor() -> SecuritySensorInterface:
    """Initialize Solana blockchain security sensor with flexible RPC configuration"""
    
    # Initialize flexible RPC configuration
    rpc_config = FlexibleRPCConfig()
    
    # Get optimal RPC configuration (now returns API key too)
    primary_url, provider_name, all_endpoints, api_key = rpc_config.detect_and_configure_rpc()
    
    # Get monitored wallet addresses from environment
    monitored_wallets = []
    wallet_env_vars = [key for key in os.environ.keys() if key.startswith("MONITOR_WALLET_")]
    for wallet_var in wallet_env_vars:
        wallet_address = os.environ[wallet_var]
        if wallet_address:
            monitored_wallets.append(wallet_address)
    
    # Default to demo wallets if none specified
    if not monitored_wallets:
        monitored_wallets = [
            "7xKs1aTF7YbL8C9s3mZNbGKPFXCWuBvf9Ss623VQ5DA",
            "9mNp2bK8fG3cCd4sVhMnBkLpQrTt5RwXyZ7nE8hS1kL"
        ]
    
    logger.info(f"🛡️ Setting up SecuritySensor for {len(monitored_wallets)} wallets:")
    for wallet in monitored_wallets:
        logger.info(f"   📡 Monitoring: {wallet[:8]}...{wallet[-8:]}")
    
    logger.info(f"🚀 Primary RPC: {provider_name}")
    if api_key:
        logger.info(f"🔑 API key configured for enhanced rate limits")
    logger.info(f"🔄 Total endpoints available: {len(all_endpoints)}")
    
    # Create SecuritySensor with flexible configuration
    sensor = SecuritySensor(
        wallet_addresses=monitored_wallets,
        solana_rpc_url=primary_url,
        rpc_api_key=api_key,  # Generic API key for any provider
        rpc_provider_name=provider_name,  # Clear provider name
    )
    
    # Print detailed configuration summary
    config_summary = rpc_config.get_configuration_summary()
    logger.info(f"📊 RPC Configuration Summary:")
    logger.info(f"   🎯 Method: {config_summary['configuration_method']}")
    logger.info(f"   🔑 Detected providers: {', '.join(config_summary['detected_providers']) if config_summary['detected_providers'] else 'None'}")
    logger.info(f"   🔐 API key status: {'Configured' if config_summary['api_key_configured'] else 'Not configured'}")
    logger.info(f"   🔄 Fallback endpoints: {len(config_summary['fallback_rpcs'])}")
    
    return sensor


def extra_background_monitor_questions():
    """Ask user about background monitoring configuration"""
    questions = [
        inquirer.List(
            name="enable_background_monitor",
            message="Enable 24/7 background intelligence monitoring?",
            choices=[
                "Yes - Monitor Twitter, Reddit, blacklisted wallets",
                "No - Just real-time transaction analysis"
            ],
        )
    ]
    
    answer = inquirer.prompt(questions)["enable_background_monitor"]
    
    if answer.startswith("Yes"):
        logger.info("✅ Background monitoring will be enabled")
        logger.info("💡 Note: Twitter/Reddit API keys are optional - system works without them")
        return True
    else:
        logger.info("📊 Using real-time analysis only")
        return False


def extra_research_tools_questions(answer_research_tools):
    """Prompt for API keys needed by selected research tools"""
    questions_rt = []
    var_rt = []
    for research_tool in answer_research_tools:
        if research_tool in SERVICE_TO_ENV:
            for env in SERVICE_TO_ENV[research_tool]:
                if not os.getenv(env):
                    var_rt.append(env)
                    questions_rt.append(
                        inquirer.Text(
                            name=env, message=f"Please enter value for this variable {env}"
                        )
                    )
    if questions_rt:
        answers_rt = inquirer.prompt(questions_rt)
        for env in var_rt:
            os.environ[env] = answers_rt[env]


def extra_model_questions(answer_model):
    """Configure AI model and prompt for API keys"""
    model_naming = {
        "Mock LLM": "mock",
        "OpenAI": "openai",
        "OpenAI (openrouter)": "openai",
        "Gemini (openrouter)": "gemini",
        "QWQ (openrouter)": "qwq",
        "Claude": "claude",
    }

    if "Mock LLM" in answer_model:
        logger.info("Notice: Using mock LLM. Responses are simulated for testing.")
    elif "openrouter" in answer_model and not os.getenv("OPENROUTER_API_KEY"):
        question_or_key = [
            inquirer.Password(
                "or_api_key", message="Please enter the Openrouter API key"
            )
        ]
        answers_or_key = inquirer.prompt(question_or_key)
        os.environ["OPENROUTER_API_KEY"] = answers_or_key["or_api_key"]
    elif "OpenAI" == answer_model and not os.getenv("OPENAI_API_KEY"):
        question_openai_key = [
            inquirer.Password(
                "openai_api_key", message="Please enter the OpenAI API key"
            )
        ]
        answers_openai_key = inquirer.prompt(question_openai_key)
        os.environ["OPENAI_API_KEY"] = answers_openai_key["openai_api_key"]
    elif "Claude" in answer_model and not os.getenv("ANTHROPIC_API_KEY"):
        question_claude_key = [
            inquirer.Password(
                "claude_api_key", message="Please enter the Claude API key"
            )
        ]
        answers_claude_key = inquirer.prompt(question_claude_key)
        os.environ["ANTHROPIC_API_KEY"] = answers_claude_key["claude_api_key"]
    return model_naming[answer_model]


def extra_sensor_questions():
    """Configure security sensor for Solana monitoring with flexible RPC support"""
    # Updated to use generic RPC terminology
    rpc_providers = ["Any Solana RPC Provider (Helius, QuickNode, Alchemy, Custom)"]
    question_security_sensor = [
        inquirer.List(
            name="sensor",
            message=f"Do you have RPC API access for real-time monitoring?",
            choices=[
                "No, I'm using Mock Security Sensor for now",
                "Yes, I have RPC API keys for real-time protection",
            ],
        )
    ]
    answer_security_sensor = inquirer.prompt(question_security_sensor)
    if answer_security_sensor["sensor"] == "Yes, I have RPC API keys for real-time protection":
        # Generic RPC configuration - works with any provider
        potential_rpc_keys = [
            "HELIUS_API_KEY", 
            "QUICKNODE_API_KEY", 
            "ALCHEMY_API_KEY",
            "CUSTOM_SOLANA_API_KEY",
            "SOLANA_RPC_URL"
        ]
        
        missing_keys = [key for key in potential_rpc_keys if not os.getenv(key)]
        
        if missing_keys:
            logger.info("💡 RPC Configuration Options:")
            logger.info("   🔸 Set HELIUS_API_KEY for Helius RPC")
            logger.info("   🔸 Set QUICKNODE_API_KEY for QuickNode RPC") 
            logger.info("   🔸 Set ALCHEMY_API_KEY for Alchemy RPC")
            logger.info("   🔸 Set CUSTOM_SOLANA_RPC_URL + CUSTOM_SOLANA_API_KEY for custom RPC")
            logger.info("   🔸 Set SOLANA_RPC_URL for any RPC endpoint")
            
            configure_rpc = inquirer.confirm("Configure RPC settings now?", default=True)
            if configure_rpc:
                questions_rpc = []
                for key in ["SOLANA_RPC_URL", "HELIUS_API_KEY"]:  # Ask for the most common ones
                    if not os.getenv(key):
                        questions_rpc.append(
                            inquirer.Text(
                                name=key, 
                                message=f"Enter {key} (optional - leave blank to skip)",
                                default=""
                            )
                        )
                
                if questions_rpc:
                    answers_rpc = inquirer.prompt(questions_rpc)
                    for key, value in answers_rpc.items():
                        if value.strip():  # Only set if not empty
                            os.environ[key] = value.strip()
        
        sensor = setup_security_sensor()
        logger.info("🚀 Real-time SecuritySensor configured with flexible RPC!")
        return sensor
    else:
        logger.info("📊 Using Mock SecuritySensor for testing")
        return MockSecuritySensor(["demo_wallet"], "mock_rpc", "mock_key")


def extra_rag_questions(answer_rag):
    """Configure RAG client"""
    if answer_rag == "Yes, i have setup the RAG":
        # RAGClient expects agent_id as first parameter
        agent_id = f"security_agent_{int(time.time())}"
        
        try:
            return RAGClient(agent_id)  # Just agent_id
        except TypeError as e:
            logger.warning(f"⚠️ RAGClient constructor error: {e}")
            logger.info("📚 Falling back to Mock RAG")
            return MockRAGClient()
    else:
        logger.info("📚 Using Mock RAG for testing")
        return MockRAGClient()


async def main_security_loop(fe_data, genner, rag_client, sensor):
    """Main async loop for security agent with background monitoring"""
    
    # Initialize database
    db = SQLiteDB(db_path=os.getenv("SQLITE_PATH", "./db/security.db"))
    
    # Generate session and agent IDs
    session_id = f"security_session_{int(time.time())}"
    agent_id = f"security_agent_{fe_data['agent_name']}"
    
    logger.info(f"🆔 Session ID: {session_id}")
    logger.info(f"🤖 Agent ID: {agent_id}")
    
    # Start the enhanced security agent with background monitoring
    await start_security_agent_with_background_monitor(
        agent_type="security",
        session_id=session_id,
        agent_id=agent_id,
        fe_data=fe_data,
        genner=genner,
        rag=rag_client,
        sensor=sensor,
        db=db,
        meta_swap_api_url=os.getenv("META_SWAP_API_URL", "http://localhost:9009"),
    )


def starter_prompt():
    """Fully automated starter for security system - minimal questions"""
    
    # Only ask the essentials
    questions = [
        inquirer.Text("agent_name", message="What's the name of your security agent?", default=""),
        inquirer.List(
            name="model",
            message="Which AI model do you want to use?",
            choices=[
                "Claude",
                "OpenAI",
                "OpenAI (openrouter)",
                "Gemini (openrouter)", 
                "QWQ (openrouter)",
                "Mock LLM"
            ],
        ),
    ]
    answers = inquirer.prompt(questions)

    logger.info("🛡️ Auto-configuring security system...")
    
    # AUTO-DETECT: RAG availability
    rag_client = auto_detect_rag()
    
    # AUTO-CONFIGURE: AI model
    model_name = extra_model_questions(answers["model"])
    
    # AUTO-CONFIGURE: Security tools (no questions)
    security_research_tools = ["Solana RPC", "Threat Intelligence", "DuckDuckGo"]
    security_notifications = ["blockchain_alerts", "security_alerts", "community_reports"]
    
    logger.info(f"✅ Enabled research tools: {', '.join(security_research_tools)}")
    logger.info(f"✅ Enabled notifications: {', '.join(security_notifications)}")
    
    # AUTO-CONFIGURE: API keys (only ask if missing and needed)
    auto_configure_api_keys(security_research_tools)
    
    # AUTO-DETECT: Sensor capabilities
    sensor = auto_detect_sensor()
    
    # AUTO-CONFIGURE: Background monitoring (enable if APIs available)
    enable_background_monitor = auto_detect_background_monitoring()

    # Build configuration
    fe_data = FE_DATA_SECURITY_DEFAULTS.copy()
    fe_data["agent_name"] = answers["agent_name"]
    fe_data["research_tools"] = security_research_tools
    fe_data["notifications"] = security_notifications
    fe_data["prompts"] = fetch_default_prompt(fe_data, "security")
    fe_data["model"] = model_name
    fe_data["enable_background_monitor"] = enable_background_monitor

    # Initialize AI generators
    genner = get_genner(
        backend=fe_data["model"],
        or_client=get_openrouter_client(),
        anthropic_client=get_anthropic_client(),
        stream_fn=lambda token: print(token, end="", flush=True),
    )
    
    logger.info("🚀 Starting Fully Automated AI Security System...")
    asyncio.run(main_security_loop(fe_data, genner, rag_client, sensor))


def auto_detect_rag():
    """Auto-detect RAG availability"""
    rag_url = os.getenv("RAG_SERVICE_URL", "http://localhost:8080")

    try:
        # Test if RAG service is running
        response = requests.get(f"{rag_url}/health", timeout=2)
        if response.status_code == 200:
            logger.info(f"✅ RAG service detected at {rag_url}")

            # Try to create RAGClient with different constructor patterns
            agent_id = f"security_agent_{int(time.time())}"
            session_id = f"security_session_{int(time.time())}"

            try:
                return RAGClient(agent_id)
            except TypeError:
                try:
                    return RAGClient(agent_id, session_id, rag_url)
                except TypeError:
                    try:
                        return RAGClient(session_id, rag_url)
                    except TypeError:
                        logger.warning("⚠️ RAGClient constructor mismatch, using Mock RAG")
                        # Fix MockRAGClient constructor
                        return MockRAGClient(agent_id, session_id)
        else:
            logger.info("📚 RAG service not available, using Mock RAG")
            # Fix MockRAGClient constructor
            agent_id = f"security_agent_{int(time.time())}"
            session_id = f"security_session_{int(time.time())}"
            return MockRAGClient(agent_id, session_id)

    except Exception as e:
        logger.info(f"📚 RAG auto-detection failed ({e}), using Mock RAG")
        # Fix MockRAGClient constructor
        agent_id = f"security_agent_{int(time.time())}"
        session_id = f"security_session_{int(time.time())}"
        return MockRAGClient(agent_id, session_id)


def auto_configure_api_keys(research_tools):
    """Only ask for API keys if they're missing and actually needed"""
    missing_keys = []
    
    for tool in research_tools:
        if tool in SERVICE_TO_ENV:
            for env_var in SERVICE_TO_ENV[tool]:
                if not os.getenv(env_var):
                    missing_keys.append(env_var)
    
    if missing_keys:
        logger.info(f"💡 Optional API keys missing: {', '.join(missing_keys)}")
        logger.info("📊 System will work without them, using fallback methods")
        
        # Only ask if user wants to provide them
        if inquirer.confirm("Configure optional API keys for enhanced features?", default=False):
            extra_research_tools_questions(research_tools)


def auto_detect_sensor():
    """Auto-detect sensor capabilities with flexible RPC support"""
    rpc_config = FlexibleRPCConfig()
    detected_providers = rpc_config._get_detected_providers()

    if detected_providers or os.getenv("CUSTOM_SOLANA_RPC_URL") or os.getenv("SOLANA_RPC_URL"):
        provider_names = ', '.join(detected_providers) if detected_providers else 'configured RPC'
        logger.info(f"🛡️ Real-time sensor available with {provider_names}")
        return setup_security_sensor()
    else:
        logger.info("📊 Using Mock sensor (add RPC configuration for real-time protection)")
        logger.info("💡 Supported providers: Helius, QuickNode, Alchemy, Custom RPCs")
        return MockSecuritySensor(["demo_wallet"], "mock_rpc", "mock_key")


def auto_detect_background_monitoring():
    """Auto-detect background monitoring capabilities"""
    if os.getenv("TWITTER_BEARER_TOKEN") or os.getenv("REDDIT_CLIENT_ID"):
        logger.info("📡 Background monitoring APIs available")
        return True
    else:
        logger.info("📊 Background monitoring disabled (add Twitter/Reddit keys to enable)")
        return False


def get_openrouter_client():
    """Get OpenRouter client if available"""
    return OpenRouter(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        include_reasoning=True,
    ) if os.getenv("OPENROUTER_API_KEY") else None


def get_anthropic_client():
    """Get Anthropic client if available"""
    return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")) if os.getenv("ANTHROPIC_API_KEY") else None

if __name__ == "__main__":
    starter_prompt()