'use strict';
// Timers cover headers AND body consumption. A response header alone is not success.
const MeetingTransport = (() => {
  async function requestJSON(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, {...options, signal: controller.signal});
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || `HTTP ${response.status}`);
        error.modelState = data.model_state;
        throw error;
      }
      return data;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('Request timed out. Device completion is not confirmed.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  return {requestJSON};
})();
if (typeof module !== 'undefined') module.exports = MeetingTransport;
