# One line to add to the Evolus agent instructions

Open your review agent in the Evolus workspace and ADD this single line to its instructions. Do not remove or reword
the existing "return only JSON" instruction: VYRA's parser expects one JSON object and nothing else.

    Check the copy against the attached VYRA brand rules library and name the source document in the "evidence" or "suggestion" text of each finding. Still return only the JSON object.

Why the second sentence: with a knowledge library attached, agents sometimes add citations or extra text around the
answer. VYRA can only read a reply that is a JSON object, and it falls back to the next model if it cannot.
