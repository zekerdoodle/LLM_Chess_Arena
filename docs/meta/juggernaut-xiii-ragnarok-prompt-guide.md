# Prompt Guide for Juggernaut XIII: Ragnarok by RunDiffusion
Source: https://learn.rundiffusion.com/prompt-guide-for-juggernaut-xiii-ragnarok-by-rundiffusion/
Domain: learn.rundiffusion.com
Fetched: 2025-09-04T15:01:00.071864Z
Status: 200
Saved: vault/docs/webresults/20250904-150100_learn-rundiffusion-com_prompt-guide-for-juggernaut-xiii-ragnarok-by-rundiffusion.md
---
This is a sequel to the earlier guides for [Juggernaut X](https://learn.rundiffusion.com/prompting-guide-for-juggernaut-x/), and the combined guide for [XI, and XII](https://learn.rundiffusion.com/prompt-guide-for-juggernaut-xi-and-xii/). Please review the earlier guides to get a foundation for the prompting styles of the Juggernaut series if you need additional help.

# **Team Juggernaut**

Team Juggernaut started with Kandoo, the creator of the Juggernaut Series of Models. Darin handles our partnership with RunDiffusion, distribution, and more.  
Adam (your guide author) handles the guides, model testing, and social media.  
We continue to be partnered and fully integrated into RunDiffusion. In addition to the team members listed, we have an amazing team at RunDiffusion supporting us with sales, promotions, and technical support. Once again, I want to thank each team member and RunDiffusion for their contribution to improving the Juggernaut series of models.

# **Introduction**

Juggernaut XIII: Ragnarok is an advanced AI model designed for professionals who need precise control, as well as hobbyists who want high-quality outputs.  
It accepts a wide variety of prompting styles. Detailed and specific prompts allow a generative prompt engineer to create true masterpieces.  
However, the model is flexible enough that even basic prompts will create beautiful images.

**Audience:** Ideal for artists, designers, and visual professionals who require control over their image outputs.  
**Prompting Style:** Detailed and specific prompts are best, but simpler prompts can still perform well. This version also supports BOORU style tokens.

# **Juggernaut XIII: Ragnarok Settings**

**Resolution:** 832 x 1216 for Portraits, but all standard SDXL resolutions work well.  
**Sampler:** DPM++ 2M SDE or DPM++2m Karras  
**Steps:** 30–40  
**CFG:** 3–6 (Lower values for realism)  
**Negative Prompt:** You may want to add NSFW tokens to the negative to be sure you don't get NSFW content.   
**VAE:** Baked in  
**HiRes:** 4xNMKD-Siax\_200k with 15 Steps, 0.3 Denoise, 1.5x Upscale

# **Prompting Tips for Ragnarok**

**Traditional Prompting Styles:** Tokens or sentences are both supported.  
**Importance of the First Sentence:** Sets the foundation for the image.  
**Use of Weights:** Apply weights sparingly to primary subjects if your application supports it.  
**Prompt Size:** Try not to exceed 75 tokens.  
**Tokens:** Use trigger words.  
**BOORU:** Booru style tokens can be used for NSFW.  
**Common Triggers:** Skin Textures, Photograph, High Resolution, Cinematic, Bad Hands, Bad Eyes.

# **Components of a Prompt**

### **Subject**

The primary focus of the image — the main character, creature, or item.   
*Examples:* *woman, warrior, fox, spaceship, garden*

### **Action**

Describes what the subject is doing — adds movement or storytelling.   
*Examples:* *running, flying, standing, meditating, sitting*

### **Environment/Setting**

The surrounding world where the subject is placed.   
*Examples:* *forest, ancient ruins, futuristic city, desert landscape*

### **Object**

Important secondary items that appear in the scene.   
*Examples:* *sword, book, crystal orb, lantern, chair*

### **Color**

Dominant colors in the scene or specific to elements.   
*Examples:* *vibrant red, pastel blue, golden light, monochrome*

### **Style**

The artistic approach or aesthetic of the image.   
*Examples:* *surrealism, watercolor, cinematic, anime-style, photorealism*

### **Mood/Atmosphere**

The emotional tone or feeling of the scene.   
*Examples:* *dark, whimsical, serene, ominous, cheerful*

### **Lighting**

The type of light affecting the scene’s mood and texture.   
*Examples:* *golden hour, soft diffused lighting, dramatic lighting, backlight, rim light*

### **Perspective/Viewpoint**

The angle or distance from which the viewer sees the subject.   
*Examples:* *close-up, bird’s eye view, wide shot, low angle*

### **Texture/Material**

Surface details — what things "feel" like visually.   
*Examples:* *metallic texture, rough stone, silky fabric, cracked leather*

### **Time Period**

The historical or futuristic era reflected in the image.   
*Examples:* *medieval, cyberpunk future, 1970s retro, Victorian era*

### **Cultural Elements**

Specific cultural features to ground or stylize the scene.   
*Examples:* *samurai armor, Nordic runes, Mayan temple, Venetian mask*

### **Emotion**

Expressed feeling in the subject (especially portraits).   
*Examples:* *joyful, melancholic, intense gaze, anger, serenity*

### **Medium**

The "art tool" or "art style" the piece mimics.   
*Examples:* *oil painting, digital illustration, pencil sketch, ink wash*

### **Clothing**

The style and type of attire worn by the subject.   
*Examples:* *royal gown, futuristic armor, tattered cloak, modern streetwear*

### **Text**

Literal readable text generated inside the image (like signs, posters, or logos). *Examples:* *text "PROMPT GUIDE", logo "Juggernaut", billboard text, graffiti letters, typography.*

Let’s examine some raw examples without upscaling or high-res fixes Be aware I tried to keep simple prompts to focus on each of the topics. For best results you would combine these elements for a comprehensive prompt.

## **A Ballerina (Subject)**

**Prompt:** An elegant ballerina dancing under a spotlight, graceful posture, flowing white dress, soft expression  
**Negative Prompt:** bad eyes, blurry, missing limbs, bad anatomy, cartoon  
  
We want to keep our subject at the start of the prompt not the end.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_O1gH0NJRLxHEWVfg2F1B_0cb4ab87-5be8-4556-8438-c6181742e4e2-1.png)

## **Cyborg Cat(Action)**

**Prompt Example:** Photograph of a cyborg cat sprinting full-speed across a rain-soaked neon street at night, sleek chrome and carbon fiber limbs, motion blur trailing behind  
**Negative Prompt:** cartoon, plastic texture, low-detail metal, broken limbs, blurry face  
  
Using dynamic actions like sprinting can help to make a scene more animated. The action should come after the subject.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_aKXqEVphuA0gFgTUtOyW_c7108d3f-6bd7-4ced-8bd9-14d7983a8c78-1.png)

## **Ancient Forest (Environment)**

**Prompt Example:** An ancient forest shrouded in morning mist, sunlight piercing through tall gnarled trees, moss covering the forest floor  
**Negative Prompt:** cartoon, low detail, plastic trees, blurry

For scenes add specific tokens that can tell your story. A few environmental tokens can go a long way. The environment can be added to the start or ending of a prompt depending on how important it is to your final image.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_VGEbfWiBK5fxTjCkLDUx_a6da4f0e-372a-4f3b-a00b-5e8aa1a4c81a-2.png)

## **Gold and Crystal Key (Object)**

**Prompt Example:** Photograph of gold and crystal glowing key pulsing with bright light  
**Negative Prompt:** blurry, bad quality  
  
With a simple prompt like this it is easy for the AI to create the object but you will get a wide variation of scenes because we did not specify the surroundings and lighting. Refining this prompt with environment and a background may yield better results. Best practice is adding objects to the first two sentences of a prompt.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_feAmt5NU6FGZRkebgO5H_1207b833-18b3-4d85-8b13-ee4d383a19d7.png)

## **Poppy Flower (Color)**

**Prompt Example:** Close up a large beautiful red and purple poppy blossom in a crystal vase, background black, dramatic lighting  
**Negative Prompt:** blurry, bad quality  
  
If you do not include a color preference the AI may use the most common color associated with your subjects in the training data. By prompting specific colors the results can really stand out.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_oxpMYcRlDL07tkK6WT5F_af7f9326-f772-4a27-9d9f-e63459989c8d.png)

## **Stone Whales (Styles)**

**Prompt Example:** Surreal photograph of Giant whales made of stone flying through a blue sky with white clouds  
**Negative Prompt:** blurry, bad quality

If you prompted just stone whales it would most likely place the whales on the ground. By adding Surreal photograph the AI knows it can be a little more free. Styles can really direct the direction the AI takes in composing your images.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_XRYY0iH7qjR63RouudBH_646b3651-d024-40a8-9863-ff79268aed79.png)

## **Gloomy (Mood)**

**Prompt Example:** A lone figure standing in a foggy graveyard at night, surrounded by dead trees and broken tombstones, eerie atmosphere, grim mood, dark cinematic lighting  
**Negative Prompt:** bright lighting, cheerful mood, cartoon, low detail, vibrant colors  
  
The mood can be captured with your prompt or by explicitly telling the model the mood you want to reinforce the mood. This is a dark mood so I prompted "Eerie atmosphere" and "Grim Mood."

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_KHdHa7noodn3TJ5JLCis_3a67e05a-df27-4d65-b0c2-fbe3c615b9f1-1.png)

## **Grandmother (Lighting)**

**Prompt Example:** Close-up of an elderly woman's face, deep wrinkles highlighted by dramatic lighting, strong shadows across her features, intense and somber mood  
**Negative Prompt:** bad eyes, flat lighting, cartoon, low detail, smooth skin, overexposed

Lighting can make or break your scene. Experiment with a lot of different lighting styles to get a feel for them.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_FyeSctxgS2AIPskUFPNF_d295f651-b23d-4d22-90c2-58b3a0128b42.png)

## **Knight (Viewpoint)**

**Prompt Example:** Looking up at a towering knight in shining armor, dramatic low-angle view, sunlight glinting off polished steel  
**Negative Prompt:** flat angle, low detail, cartoon, broken armor

Perspective and viewpoints are important to bring variety to your AI images. Use common camera techniques and views in your prompt.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_HVEOC37vKgoYYJ99KAMu_99c70a22-6829-4d88-adf2-58a25cc610ca.png)

## **Fabric (Textures)**

**Prompt Example:** Close-up of intricate embroidered fabric, fine threads and delicate stitching visible, soft natural light highlighting the textures  
**Negative Prompt:** smooth surface, blurry details, cartoon, low detail, plastic texture

Adding specific textures to your prompts can add hidden details to improve your image quality. This is an often overlooked portion of prompting.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_X07dgWpbTCmf0G39GW4F_0329b25d-1a66-41c0-a2a4-bb192842ab32-1.png)

## Soldier (**Time Period)**

**Prompt Example:** A WWII soldier sitting in a muddy trench, worn uniform and helmet covered in dirt  
**Negative Prompt:** modern military gear, futuristic weapons, cartoon, low detail, clean, bad hands, deformed

Adding time period can change the aesthetic of an image. By adding a time period like WW2 it will also change the feel and mood of an image. This is not necessary but can be the missing piece to your prompt.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_OsLiVx3GlIFjOtCWj3x0_307a056d-f3a0-4ac4-9480-7e5db6d0ebd0-1.png)

## **Cherry Blossom (Cultural Elements)**

**Prompt Example:** A traditional Japanese shrine surrounded by cherry blossoms in full bloom, a woman in a flowing kimono walking along a stone path  
**Negative Prompt:** western clothing, modern buildings, cartoon, low detail

You can add cultural elements like Japanese, Viking, and Western. Doing that will guide the model towards cultural elements and styles from those cultures.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_IBx41bHcseCkxtNTw6Hh_3497c0d7-52be-4921-add8-9b349704c456.png)

## **Robot (Emotion)**

**Prompt Example:** mid shot photograph of a happy robot wearing a white dress, golden hour, high resolution  
**Negative Prompt:** bad eyes, expressionless face, sadness, cartoon, low detail, deformed hands

Adding emotions like angry, happy and sad can do more than add a facial expression but add a feeling to an image. Even a robot can be made to look joyful with a happy token added.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_dAZaU9HFedi2aXHPWirI_332af825-200a-4b1e-bef4-d2de663e7a5c.png)

## **Village by the Sea (Medium)**

**Prompt Example:** A watercolor painting of a small village by the sea, blending pastel blues and greens, gentle flowing textures across the sky and water  
**Negative Prompt:** photorealistic, oil painting, cartoon, sharp hard edges, low detail  
  
This is a very important part of a prompt. Photograph, Painting are as important as style and subject. Experiment with the various artistic mediums.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_WwS0ksuUfO5vX9CyVZ7R_9240636c-20de-445e-b922-41f59efd4a72.png)

## **Alien Ball (Clothing)**

**Prompt Example:** A tall alien wearing a glittering ball gown covered in iridescent patterns, standing under strange colorful lights  
**Negative Prompt:** human clothing, dull colors, cartoon, low detail, medieval armor, nudity, naked  
  
Be sure to add clothing. This model is trained on NSFW images so prompting clothing is important to be sure you don't accidentally get NSFW images.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_ddsKrChc5Sea3I1Yi3m3_128015f4-d636-4468-8088-25a4214fd72a.png)

## **Metallic Typography (Text)**

**Prompt Example:** The word "Guide" in bold metallic letters at the center of the image, A single Metal flower beneath a stormy sky, High resolution  
**Negative Prompt:** blurry, cartoon, low detail, cheerful colors  
  
Adding text, typography, words to text is possible but SDXL is not the best model for text. We know that is a limitation of this model but it is possible but it may take several generations. Make sure you put the text at the front of the prompt not at the end.

![](https://learn.rundiffusion.com/content/images/2025/05/runnitResults_users_33LfEDdxlyZufi5rhbzABHFSLNT2_runs_NLAmYwO5frdfsvKkOWC7_b27f7b7d-34c2-4d0a-afb1-f0d52c665a5b.png)

# **Rules**

Remember: You don’t have to include every single element for beautiful results. The more specific you are, the more control you’ll have. The more vague you are, the more flexibility the model will show.

## **Keep it Safe for work**

Managing your prompt carefully—especially your negative prompt—will help avoid unexpected or unintended NSFW results.

**BOORU Token Influence:**  
Juggernaut Ragnarok was trained with BOORU style tokens for anatomical detail.  
If specific BOORU tokens are used positively, it may trigger unwanted outputs. To steer clear, ensure such tokens are placed in the negative.

**Natural Realism Tokens:**  
Tokens like *detailed skin*, *highlighted skin*, *natural* , and *realistic texture* enhance anatomical fidelity.Being mindful about where these tokens are included can help control the outcome.

**Always prompt Clothing:**  
An effective way to further prevent unwanted content is to clearly describe clothing in the positive prompt. Tokens such as *elegant dress*, *battle armor*, *casual streetwear*, or *formal suit* direct the model’s focus toward fully clothed, detailed outfits.

## **SFW Best Practices**

* Always filter anatomical tokens into your negative prompts.
* Include clothing descriptions in positive prompts to anchor safe imagery.
* Use realism-enhancing tokens carefully.
* Keep prompts clear, specific, and focused on non-anatomical subjects.
* Use services like **Runnit** on RunDiffusion that has NSFW filter.

RunDiffusion enforces policies to block most NSFW generations at the platform level. If you're working locally, take extra care to set up your prompts thoughtfully.  
We encourage responsible prompting to ensure all content remains appropriate for your project goals.

# **Conclusion**

We hope you enjoy creating with Juggernaut XIII: Ragnarok. I want to sincerely thank RunDiffusion for their ongoing support for Team Juggernaut, sponsorship, and partnership. Their commitment to innovation and creator empowerment has allowed Team Juggernaut to continue refining our craft and delivering new tools to the community. And of course a great appreciation for Kandoo for all his work. It is through this collaboration that models like Ragnarok can exist. We encourage you to explore, experiment, and push the boundaries of your creativity.  
  
Your feedback, ideas, and artistry encourage us to keep innovating. Thank you again for being part of this journey — and we can't wait to see what you create.

### **Try it now** on [RunDiffusion](https://www.rundiffusion.com/?ref=learn.rundiffusion.com) Additional resources

**A basic text to image workflow with an upscale.**

![](https://learn.rundiffusion.com/content/images/2025/05/ComfyUI_01877_.png)

**Pixelwave for Text/Vibrant Colors + Juggernaut for Photorealism and details.**

![](https://learn.rundiffusion.com/content/images/2025/05/image-63.png)

**Flux Dev for Text + Juggernaut for Details**

![](https://learn.rundiffusion.com/content/images/2025/05/image-64.png)

**Common Tokens for Jug XII**

You can check online for a list of BOORU tags if you need them.