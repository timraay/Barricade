const API_URL = "http://localhost:8080/reports/submit";

function extractPlayerData(arr, start) {
  return {
    name: arr[start],
    id: arr[start + 1],
    bmRconUrl: arr[start + 2],
  };
}

function sendResponse(e) {
  const itemResponses = e.response.getItemResponses();
  const token = itemResponses[0];
  const players = [extractPlayerData(itemResponses, 1)];
  const reasons = itemResponses[4].split(";");
  const description = itemResponses[5];
  // const attachmentUrls = itemResponses[6].split(";");
  // for (const i = 7; i < 7 + 4 * 4; i += 4) {
  const attachmentUrls = [];
  for (const i = 6; i < 6 + 4 * 4; i += 4) {
    if (!itemResponses[i]) break;
    players.push(extractPlayerData(itemResponses, i + 1));
  }

  const isEdit = itemResponses[itemResponses.length - 1] === "1";

  const data = {
    id: e.response.getId(),
    timestamp: e.response.getTimestamp(),
    data: {
      token: token,
      players: players,
      reasons: reasons,
      body: description,
      attachmentUrls: attachmentUrls
    }
  };

  const options = {
    method: isEdit ? "put" : "post",
    payload: JSON.stringify(data),
    contentType: "application/json; charset=utf-8",
  };

  UrlFetchApp.fetch(API_URL, options);
};