pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    environment {
        // --- 1. 业务环境与测试环境分离：保留业务 DATABASE_URL，只新增测试连接串 ---
        //业务相关
        DATABASE_URL          = "${params.DATABASE_URL ?: ''}"
        REDIS_PASSWORD        = "${params.REDIS_PASSWORD ?: ''}"
        WX_APP_SECRET         = "${params.WX_APP_SECRET ?: ''}"
        SMTP_PASSWORD         = "${params.SMTP_PASSWORD ?: ''}"
        ALI_ACCESS_KEY_SECRET = "${params.ALI_ACCESS_KEY_SECRET ?: ''}"
        DEBUG_MASTER_PASSWORD = "${params.DEBUG_MASTER_PASSWORD ?: ''}"
        REDIS_HOST            = "${params.REDIS_HOST ?: ''}"
        //测试相关
        TEST_DATABASE_HOST    = "${params.TEST_DATABASE_HOST ?: ''}"
        TEST_DATABASE_PORT    = "${params.TEST_DATABASE_PORT ?: '3306'}"
        TEST_DATABASE_USER    = "${params.TEST_DATABASE_USER ?: ''}"
        TEST_DATABASE_PASSWORD = "${params.TEST_DATABASE_PASSWORD ?: ''}"
        TEST_DATABASE_BOOTSTRAP_DB = "${params.TEST_DATABASE_BOOTSTRAP_DB ?: 'testdb_0'}"

        // --- 2. 固定配置 ---
        EMAIL_FROM  = 'bang@honguo.store'
        SMTP_SERVER = 'smtpdm.aliyun.com'
        SMTP_PORT   = '465'
        SMTP_USER   = 'bang@honguo.store'
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

        SSH_PRIVATE_KEY  = "${params.SSH_PRIVATE_KEY ?: ''}"
    }

    stages {
        stage('0. 准备数据库连接串') {
            steps {
                script {
                    if (!TEST_DATABASE_HOST?.trim() || !TEST_DATABASE_USER?.trim()) {
                        error '请先填写 TEST_DATABASE_HOST / TEST_DATABASE_USER / TEST_DATABASE_PASSWORD / REDIS_HOST 等测试参数'
                    }

                    def encodedPassword = java.net.URLEncoder.encode(TEST_DATABASE_PASSWORD, 'UTF-8')
                    env.TEST_DATABASE_URL = "mysql+aiomysql://${TEST_DATABASE_USER}:${encodedPassword}@${TEST_DATABASE_HOST}:${TEST_DATABASE_PORT}/${TEST_DATABASE_BOOTSTRAP_DB}"
                    echo "✓ 测试数据库连接串已准备完成（业务 DATABASE_URL 保持不变）"
                }
            }
        }

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
                    python -m pip install --upgrade pip
                    python -m pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
                    uv pip install --python "$(which python)" -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
                '''
            }
        }

        stage('3. 连通性测试') {
            steps {
                echo "Status: 执行 Python 脚本校验中间件..."
                sh '''
                    set -e
                    . .venv/bin/activate
                    export PYTHONPATH="$WORKSPACE${PYTHONPATH:+:$PYTHONPATH}"
                    python - <<'PY'
import asyncio
import sys
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
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

        stage('4. 单元测试') {
            steps {
                echo "Status: 执行单元测试..."
                sh '''
                    set -e
                    . .venv/bin/activate
                    export PYTHONPATH="$WORKSPACE${PYTHONPATH:+:$PYTHONPATH}"
                    mkdir -p test_reports
                    
                    echo "=== 运行单元测试层 ==="
                    pytest tests/unit \
                        --junitxml=test_reports/unit_report.xml \
                        --cov=app \
                        --cov-append \
                        --cov-report= \
                        -v \
                        --tb=short \
                        -rs

                    python scripts/validate_junit_report.py test_reports/unit_report.xml "unit"
                    echo "✓ 单元测试完成"
                '''
            }
        }

stage('5. 集成测试') {
            steps {
                script {
                    def realUser = env.TEST_DATABASE_USER ?: params.TEST_DATABASE_USER
                    def realHost = env.TEST_DATABASE_HOST ?: params.TEST_DATABASE_HOST
                    def realPort = env.TEST_DATABASE_PORT ?: params.TEST_DATABASE_PORT ?: '3306'
                    def realDb   = env.TEST_DATABASE_BOOTSTRAP_DB ?: params.TEST_DATABASE_BOOTSTRAP_DB ?: 'testdb_0'
                    def encodedPassword = java.net.URLEncoder.encode(env.TEST_DATABASE_PASSWORD ?: params.TEST_DATABASE_PASSWORD, 'UTF-8')
                    
                    def runUrl = "mysql+aiomysql://${realUser}:${encodedPassword}@${realHost}:${realPort}/${realDb}"

                    sh """
                        set -e
                        . .venv/bin/activate
                        export PYTHONPATH="\$WORKSPACE"
                        mkdir -p test_reports

                        echo "=== 🚀 工业级多租户隔离集成测试开始 ==="
                        
                        echo "[DEBUG] 本次 Gerrit 门禁构建号: \${BUILD_NUMBER:-local}"
                        
                        # 强行死锁写入测试连接串
                        export TEST_DATABASE_URL="${runUrl}"

                        # 交付 Pytest 轰击
                        pytest tests/integration \
                            --junitxml=test_reports/integration_report.xml \
                            --cov=app \
                            --cov-append \
                            --cov-report= \
                            -v \
                            --tb=short \
                            -rs

                        python scripts/validate_junit_report.py test_reports/integration_report.xml "integration"
                    """
                }
            }
        }

        stage('6. 覆盖率汇总') {
            steps {
                echo "Status: 生成覆盖率汇总..."
                sh '''
                    set -e
                    . .venv/bin/activate
                    export PYTHONPATH="$WORKSPACE${PYTHONPATH:+:$PYTHONPATH}"

                    coverage xml -o test_reports/coverage.xml
                    coverage html -d test_reports/coverage_html
                    coverage report -m

                    echo "✓ 覆盖率报告已生成"
                '''
            }
        }
    }

    post {
        success {
            script {
                echo "Verify +1: 测试通过"
                if (env.GERRIT_PATCHSET_REVISION) {
                    sh "${GERRIT_BASE_CMD} --verified +1 --message '\"Jenkins: Build Success [SUCCESS]\"'"
                } else {
                    echo "Skip Gerrit verify +1: GERRIT_PATCHSET_REVISION is empty"
                }
            }
        }
        failure {
            script {
                echo "Verify -1: 测试失败 / 存在 SKIPPED 或 ERROR"
                if (env.GERRIT_PATCHSET_REVISION) {
                    sh "${GERRIT_BASE_CMD} --verified -1 --message '\"Jenkins: Build Failed — 单元/集成测试未全部通过（含 SKIPPED/ERROR）\"'"
                }
            }
        }
        always {
          
            junit(
                testResults: 'test_reports/*_report.xml',
                allowEmptyResults: true,
                keepLongStdio: true,
                skipPublishingChecks: false
            )
            
            publishHTML([
                allowMissing: true,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: 'test_reports/coverage_html',
                reportFiles: 'index.html',
                reportName: 'Coverage Report'
            ])
            
            cleanWs()
        }
    }
}