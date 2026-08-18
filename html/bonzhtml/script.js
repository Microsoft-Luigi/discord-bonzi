
const FRAME_WIDTH = 200;
const FRAME_HEIGHT = 160;

/*
 * Change this if you generated
 * the atlas with a different column count.
 */
const COLS = 36;

let currentFrame = 0;

const atlas = new Image();
atlas.src = "bonzi_atlas.webp";

const canvas = document.getElementById("viewer");
const ctx = canvas.getContext("2d");

atlas.onload = () => {
    drawFrame(0);
};




function playFramesLooping(frames, fps)
{
    let current = 0;

    setInterval(() =>
    {
        drawFrame(frames[current]);

        current++;

        if (current >= frames.length)
            current = 0;

    }, 1000 / fps);
}


function playFrames(frames, fps)
{


    let current = 0;

    const animation_timer = setInterval(() =>
    {
        drawFrame(frames[current]);

        current++;

        if (current >= frames.length)
        {
            clearInterval(animation_timer);

            //if (onFinished)
            //    onFinished();
        }

    }, 1000 / fps);
}

function drawFrame(frameId)
{
    currentFrame = parseInt(frameId);

    const col = currentFrame % COLS;
    const row = Math.floor(currentFrame / COLS);

    const sx = col * FRAME_WIDTH;
    const sy = row * FRAME_HEIGHT;

    ctx.clearRect(
        0,
        0,
        canvas.width,
        canvas.height
    );

    ctx.drawImage(
        atlas,
        sx,
        sy,
        FRAME_WIDTH,
        FRAME_HEIGHT,
        0,
        0,
        FRAME_WIDTH,
        FRAME_HEIGHT
    );

    document.getElementById("frameInput").value =
        currentFrame;

    document.getElementById("info").innerHTML =
        `
        Frame: ${currentFrame}<br>
        Column: ${col}<br>
        Row: ${row}<br>
        Atlas X: ${sx}<br>
        Atlas Y: ${sy}
        `;
}

function setFrame(frame)
{
    drawFrame(Number(frame));
}

function previousFrame()
{
    drawFrame(
        Math.max(0, currentFrame - 1)
    );
}

function nextFrame()
{
    drawFrame(currentFrame + 1);
}

document.addEventListener(
    "keydown",
    e =>
    {
        if (e.key === "ArrowLeft")
            previousFrame();

        if (e.key === "ArrowRight")
            nextFrame();

        if (e.key === "ArrowUp")
            playFrames([344,345,346,347,348,349,350,351,352,353,354,355], 12);
    }
);