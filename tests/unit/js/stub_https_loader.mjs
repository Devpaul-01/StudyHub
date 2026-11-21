// Node ESM hooks: replace remote (https:) imports such as the Socket.IO CDN build
// with an inert stub so frontend modules can be imported offline in tests.
const STUB = 'data:text/javascript,' + encodeURIComponent(
  'const sock = { on(){}, off(){}, emit(){}, connected:false };' +
  'export const io = () => sock; export default io;'
);
export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('https:') || specifier.startsWith('http:')) {
    return { url: STUB, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
