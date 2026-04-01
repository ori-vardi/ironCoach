export const AGENT_LABELS = {
  'main-coach': 'IronCoach',
  'run-coach': 'Run Coach',
  'swim-coach': 'Swim Coach',
  'bike-coach': 'Bike Coach',
  'nutrition-coach': 'Nutrition Coach',
  'main-dev': 'Lead Dev',
  'frontend-dev': 'Frontend Dev',
  'backend-dev': 'Backend Dev',
  'code-simplifier': 'Simplify',
  'security-reviewer': 'Security',
  'frontend-reviewer': 'FE Review',
  'backend-reviewer': 'BE Review',
  'data-reviewer': 'Data Review',
  'chat': 'Chat',
  'insight': 'Insights',
  'meal-analysis': 'Meal Analysis',
  'title-gen': 'Title Gen',
  'chat-summary': 'Chat Summary',
  'haiku-qa': 'Haiku QA',
}

export const COACH_AGENT_LABELS = {
  'main-coach': 'IronCoach',
  'run-coach': '🏃 Run',
  'swim-coach': '🏊 Swim',
  'bike-coach': '🚴 Bike',
  'nutrition-coach': '🍽 Nutrition',
}

export const DEV_AGENT_LABELS = {
  'main-dev': '⚡ Lead Dev',
  'frontend-dev': '🖥 Frontend',
  'backend-dev': '🐍 Backend',
  'code-simplifier': '✨ Simplify',
  'security-reviewer': '🛡 Security',
  'frontend-reviewer': '🎨 FR Review',
  'backend-reviewer': '🔧 BE Review',
  'data-reviewer': '📋 Data Review',
}

export function getAgentLabels(mode) {
  return mode === 'dev' ? DEV_AGENT_LABELS : COACH_AGENT_LABELS
}
