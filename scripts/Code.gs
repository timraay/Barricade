const API_URL = "http://localhost:8080/reports/submit";
const TOKEN_LEN = 22;

function extractPlayerData(arr, start) {
  return {
    playerName: arr[start],
    playerId: arr[start + 1],
    bmRconUrl: arr[start + 2] || null,
  };
}

function sendResponse(e) {
  const itemResponses = e.response.getItemResponses().map(r => r.getResponse());
  const token = itemResponses[0].slice(0, TOKEN_LEN);
  const players = [extractPlayerData(itemResponses, 1)];
  const reasons = itemResponses[4];
  const description = itemResponses[5];
  // const attachmentUrls = itemResponses[6];
  // for (let i = 7; i < 7 + 4 * 4; i += 4) {
  const attachmentUrls = [];
  for (let i = 6; i < 6 + 4 * 4; i += 4) {
    if (!itemResponses[i]) break;
    players.push(extractPlayerData(itemResponses, i + 1));
  }

  const isEdit = (itemResponses[0].slice(TOKEN_LEN) || itemResponses[itemResponses.length - 1]) === "1";

  const data = {
    id: e.response.getId(),
    timestamp: e.response.getTimestamp(),
    data: {
      token: token,
      players: players,
      reasons: reasons,
      body: description,
      attachmentUrls: attachmentUrls,
    }
  };

  const options = {
    method: isEdit ? "put" : "post",
    payload: JSON.stringify(data),
    contentType: "application/json; charset=utf-8",
  };

  UrlFetchApp.fetch(API_URL, options);
};