// Upserts one section of the shared Saphira PR comment. Shared by saphira-collate-comment and
// saphira-run, which load it from their own action checkout so it works from any repository.
module.exports = async ({ github, context, core }) => {
  const marker = process.env.COMMENT_MARKER || '<!-- saphira-combined-metrics-comment -->';
  const detectedParent = process.env.DETECTED_PARENT || '';
  const combinedSummary = process.env.COMBINED_SUMMARY || '### Combined Saphira Metrics\n- Summary generation failed';
  const commitSuffix = context.sha.slice(-7);
  const runAttempt = process.env.GITHUB_RUN_ATTEMPT || '1';
  const requestedSectionKey = (process.env.SECTION_KEY || '').trim();
  const fallbackSectionKey = (process.env.RESULTS_DIRECTORIES || '').trim() || 'default';
  const rawSectionKey = requestedSectionKey || fallbackSectionKey;
  const normalizedSectionKey = rawSectionKey
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'default';
  const sectionHeading = (process.env.SECTION_HEADING || '').trim() || `Combined Saphira Metrics (${normalizedSectionKey})`;
  const sectionStart = `<!-- saphira-collate-section:${normalizedSectionKey}:start -->`;
  const sectionEnd = `<!-- saphira-collate-section:${normalizedSectionKey}:end -->`;
  let artifactsLink = '';

  try {
    const artifacts = await github.paginate(
      github.rest.actions.listWorkflowRunArtifacts,
      {
        owner: context.repo.owner,
        repo: context.repo.repo,
        run_id: context.runId,
        per_page: 100,
      },
    );
    if (artifacts.length > 0) {
      const runUrl = `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`;
      artifactsLink = `[View ${artifacts.length} workflow artifact(s)](${runUrl})`;
    }
  } catch (error) {
    core.warning(`Could not list workflow artifacts: ${error.message}`);
  }

  const sectionBody = [
    sectionStart,
    `## ${sectionHeading}`,
    detectedParent ? `Detected production parent: ${detectedParent}` : '',
    `Trigger commit suffix: ${commitSuffix} (run attempt ${runAttempt})`,
    artifactsLink,
    '',
    combinedSummary,
    sectionEnd,
  ].filter((line) => line !== null && line !== '').join('\n');

  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const sectionRegex = new RegExp(`${escapeRegex(sectionStart)}[\\s\\S]*?${escapeRegex(sectionEnd)}`, 'm');

  const { data: comments } = await github.rest.issues.listComments({
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: context.payload.pull_request.number,
    per_page: 100,
  });

  const existing = comments.find((c) =>
    typeof c.body === 'string' && c.body.includes(marker)
  );

  if (existing) {
    let body = (existing.body || '').trimEnd();

    if (sectionRegex.test(body)) {
      body = body.replace(sectionRegex, sectionBody);
    } else {
      body = [body, '', sectionBody].filter((line) => line !== '').join('\n');
    }

    await github.rest.issues.updateComment({
      owner: context.repo.owner,
      repo: context.repo.repo,
      comment_id: existing.id,
      body,
    });
  } else {
    const body = [
      marker,
      '# Saphira Combined Run Results',
      'Each section below corresponds to one collate invocation in this workflow.',
      '',
      sectionBody,
    ].join('\n');

    await github.rest.issues.createComment({
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: context.payload.pull_request.number,
      body,
    });
  }
};
