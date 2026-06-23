/** Shared CI ingest quickstart snippet builder. */
export type IngestProvider =
  | "jenkins"
  | "circleci"
  | "teamcity"
  | "drone"
  | "github_actions"
  | "gitlab_ci"
  | "generic";

export const INGEST_PROVIDERS: ReadonlyArray<IngestProvider> = [
  "jenkins",
  "circleci",
  "teamcity",
  "drone",
  "github_actions",
  "gitlab_ci",
  "generic",
] as const;

/** Render the recommended copy-paste snippet for ``provider``. */
export function buildIngestSnippet(
  provider: IngestProvider,
  token: string,
  apiBase: string = "/api",
): string {
  const url = `${apiBase.replace(/\/$/, "")}/ingest`;
  switch (provider) {
    case "jenkins":
      return `// In a Jenkins declarative pipeline (post failure block):
post {
  failure {
    sh '''
      curl -fsS -X POST ${url}/jenkins \\
        -H "Authorization: Bearer ${token}" \\
        -H "Content-Type: application/json" \\
        -d "$(jq -n \\
          --arg job  "$JOB_NAME" \\
          --arg url  "$BUILD_URL" \\
          --arg log  "$(cat $WORKSPACE/build.log)" \\
          '{job:$job, build_number:'$BUILD_NUMBER', status:"FAILURE", build_url:$url, log:$log}')"
    '''
  }
}`;
    case "circleci":
      return `# .circleci/config.yml — add to any job:
- when:
    condition:
      not: << pipeline.success >>
    steps:
      - run:
          name: Send failure log to Exlogare
          when: on_fail
          command: |
            curl -fsS -X POST ${url}/circleci \\
              -H "Authorization: Bearer ${token}" \\
              -H "Content-Type: application/json" \\
              -d "$(jq -n \\
                --arg ps   "$CIRCLE_PROJECT_USERNAME/$CIRCLE_PROJECT_REPONAME" \\
                --arg wf   "$CIRCLE_WORKFLOW_ID" \\
                --arg job  "$CIRCLE_JOB" \\
                --arg log  "$(cat /tmp/build.log)" \\
                '{project_slug:"gh/"+$ps, workflow_id:$wf, job_name:$job, job_number:'$CIRCLE_BUILD_NUM', status:"failed", log:$log}')"`;
    case "teamcity":
      return `# In a TeamCity build step "Run on failure":
curl -fsS -X POST ${url}/teamcity \\
  -H "Authorization: Bearer ${token}" \\
  -H "Content-Type: application/json" \\
  -d "$(jq -n \\
    --arg bt   "%system.teamcity.buildType.id%" \\
    --arg bn   "%system.build.number%" \\
    --arg log  "$(cat build.log)" \\
    '{build_type_id:$bt, build_id:'%teamcity.build.id%', build_number:$bn, status:"failure", log:$log}')"`;
    case "drone":
      return `# .drone.yml — runs only on failure:
- name: send-failure-log
  image: alpine
  when:
    status: [ failure ]
  commands:
    - apk add --no-cache curl jq
    - |
      curl -fsS -X POST ${url}/drone \\
        -H "Authorization: Bearer ${token}" \\
        -H "Content-Type: application/json" \\
        -d "$(jq -n \\
          --arg repo "$DRONE_REPO" \\
          --arg br   "$DRONE_BRANCH" \\
          --arg sha  "$DRONE_COMMIT" \\
          --arg log  "$(cat build.log)" \\
          '{repo:$repo, build_number:'$DRONE_BUILD_NUMBER', status:"failure", branch:$br, commit_sha:$sha, log:$log}')"`;
    case "github_actions":
      return `# .github/workflows/ci.yml — add as the last step:
- name: Send failure log to Exlogare
  if: failure()
  shell: bash
  run: |
    curl -fsS -X POST ${url}/log \\
      -H "Authorization: Bearer ${token}" \\
      -H "Content-Type: application/json" \\
      -d "$(jq -n \\
        --arg pid "$GITHUB_RUN_ID" \\
        --arg jid "$GITHUB_JOB" \\
        --arg br  "$GITHUB_REF_NAME" \\
        --arg sha "$GITHUB_SHA" \\
        --arg log "$(cat build.log)" \\
        '{provider:"github_actions", project:"$GITHUB_REPOSITORY", pipeline_id:$pid, job_id:$jid, status:"failed", branch:$br, commit_sha:$sha, log:$log}')"`;
    case "gitlab_ci":
      return `# .gitlab-ci.yml — runs only when the job fails:
after_script:
  - |
    if [ "$CI_JOB_STATUS" = "failed" ]; then
      curl -fsS -X POST ${url}/log \\
        -H "Authorization: Bearer ${token}" \\
        -H "Content-Type: application/json" \\
        -d "$(jq -n \\
          --arg pid "$CI_PIPELINE_ID" \\
          --arg jid "$CI_JOB_ID" \\
          --arg br  "$CI_COMMIT_REF_NAME" \\
          --arg sha "$CI_COMMIT_SHA" \\
          --arg log "$(cat build.log)" \\
          '{provider:"gitlab_ci", project:"$CI_PROJECT_PATH", pipeline_id:$pid, job_id:$jid, status:"failed", branch:$br, commit_sha:$sha, log:$log}')"
    fi`;
    case "generic":
    default:
      return `# Minimal generic POST — works from any CI:
curl -fsS -X POST ${url}/log \\
  -H "Authorization: Bearer ${token}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "provider": "buildkite",
    "project":  "myorg/myrepo",
    "status":   "failed",
    "log":      "...build log goes here..."
  }'`;
  }
}

/** Render a quickstart snippet for the *read* side of the API */
export type ReadRecipe = "list" | "stats" | "morning_digest";

export function buildReadSnippet(
  recipe: ReadRecipe,
  token: string,
  apiBase: string = "/api",
): string {
  const root = apiBase.replace(/\/$/, "");
  const v1 = `${root}/v1`;
  switch (recipe) {
    case "stats":
      return `# Last 7 days of stats — for dashboards and morning digests:
curl -fsS \\
  -H "Authorization: Bearer ${token}" \\
  "${v1}/stats/overview?days=7"`;
    case "morning_digest":
      return `# Yesterday's RCAs (severity >= medium) — paginate via next_cursor:
SINCE=$(date -u -d "yesterday 00:00:00" +%FT%T)
UNTIL=$(date -u -d "today 00:00:00" +%FT%T)
curl -fsS \\
  -H "Authorization: Bearer ${token}" \\
  "${v1}/analyses?since=$SINCE&until=$UNTIL&severity=medium&limit=200"

# Ready-made script: /integrations/scripts/morning-digest.py`;
    case "list":
    default:
      return `# Pull recent RCAs (cursor-paginated, scope=read required):
curl -fsS \\
  -H "Authorization: Bearer ${token}" \\
  "${v1}/analyses?severity=high&limit=50"`;
  }
}
