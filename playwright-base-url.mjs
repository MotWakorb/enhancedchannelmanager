export function resolveE2EBaseURL(exactBuild, inheritedBaseURL) {
  if (exactBuild) return 'http://127.0.0.1:4173'
  return inheritedBaseURL || 'http://localhost:6100'
}
