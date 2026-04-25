pipeline {
    agent any

    environment {
        // --- 1. 敏感信息 (从参数读取，增加默认值保护) ---
        DATABASE_URL          = "${params.DATABASE_URL ?: ''}"
        REDIS_PASSWORD        = "${params.REDIS_PASSWORD ?: ''}"
        WX_APP_SECRET         = "${params.WX_APP_SECRET ?: ''}"
        SMTP_PASSWORD         = "${params.SMTP_PASSWORD ?: ''}"
        ALI_ACCESS_KEY_SECRET = "${params.ALI_ACCESS_KEY_SECRET ?: ''}"
        DEBUG_MASTER_PASSWORD = "${params.DEBUG_MASTER_PASSWORD ?: ''}"
        REDIS_HOST  = "${params.REDIS_HOST ?: ''}"

        // --- 2. 固定配置
        EMAIL_FROM  = 'tianshu@wyqsama.cn'
        SMTP_SERVER = 'smtpdm.aliyun.com'
        SMTP_PORT   = '465'
        SMTP_USER   = 'tianshu@wyqsama.cn'
        REDIS_PORT  = '6379'
        DEBUG       = 'true'
        DEBUG_SKIP_PASSWORD_CHECK = 'false'
        WX_APP_ID   = 'wx78bd4e0726460743'
        ALI_ACCESS_KEY_ID = 'LTAI5t91CbPcGqHbygsqXXhd'
        SMS_TEMPLATE_CODE = '100001'
        SMS_SIGN_NAME     = '速通互联验证码'

        // --- 3. 基础指令定义 ---
        GERRIT_BASE_CMD = "ssh -p 29418 jenkins@gerrit.lilingkun.com gerrit review ${env.GERRIT_PATCHSET_REVISION ?: ''}"
        PROJECT_URL     = "ssh://jenkins@gerrit.lilingkun.com:29418/${env.GERRIT_PROJECT ?: 'honguo-buddy-backend'}"
        PYTHONPATH      = "."
    }

    stages {
        stage('1. 环境拉取') {
            steps {
                script {
                    if (env.GERRIT_REFSPEC) {
                        checkout([$class: 'GitSCM', 
                            branches: [[name: 'FETCH_HEAD']],
                            userRemoteConfigs: [[
                                url: "${PROJECT_URL}",
                                refspec: "${env.GERRIT_REFSPEC}"
                            ]]
                        ])
                    } else {
                        checkout scm
                    }
                }
            }
        }

        stage('2. 依赖管理 (uv 提速)') {
            steps {
                sh '''
                    set -e
                    if [ ! -d ".venv" ]; then
                        python3 -m venv .venv
                    fi
                    . .venv/bin/activate
                    pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
                    uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
                '''
            }
        }

        stage('3. 连通性测试') {
            steps {
                echo "Status: 执行 Python 脚本校验中间件..."
                sh '''
                    set -e
                    . .venv/bin/activate
                    python - <<'PY'
import asyncio
import sys
import os
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# 显式加载环境变量，防止 Pydantic 漏读
from app.core.config import settings

async def check():
    print(f"Checking Redis connection to {settings.REDIS_HOST}...")
    r = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, password=settings.REDIS_PASSWORD)
    try:
        await r.ping()
        print("Success: Redis connected")
    except Exception as e:
        print(f"Error: Redis connection failed: {e}")
        sys.exit(1)
    finally:
        await r.aclose()

    print(f"Checking MySQL connection...")
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            print("Success: MySQL connected")
    except Exception as e:
        print(f"Error: MySQL connection failed: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()

asyncio.run(check())
PY
                '''
            }
        }
    }

    post {
        success {
            script {
                echo "Verify +1: 测试通过"
                sh "${GERRIT_BASE_CMD} --verified +1 --message '\"Jenkins: Build Success [SUCCESS]\"'"
            }
        }
        failure {
            script {
                echo "Verify -1: 存在错误"
                if (env.GERRIT_PATCHSET_REVISION) {
                    sh "${GERRIT_BASE_CMD} --verified -1 --message '\"Jenkins: Build Failed [FAILED]\"'"
                }
            }
        }
        always {
            cleanWs()
        }
    }
}