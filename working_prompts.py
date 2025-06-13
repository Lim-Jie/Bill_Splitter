#Specifying items only
"Can you move item 1 2 and 3 to charlie ?"

#Specifying items, source and destination
"Can you move item 1 and 2 from alice to lijie"

#Specifying source and destination only
"Can you move all items in alice to lijie"

#Specifying percentage divide based on individuals
"Can you split the bill 30,30,40 to alice, lijie and charlie?"

#Divide the bill in x number of ways
"Can you split the bill in 3 ways"

#Divide the bill with 1 person only (3 participants)
"Can you split the bill 1 way"

#Divide the bill with 2 ppl only (3 participants)
"Can you divide the bill 2 ways"

#Divide the bill equally
"Can you divide the bill 3 ways"

#Divide the bill equally
"Can you equally divide the bill"






#[CANNOT WORK]
#Specifying destination only 
"Can you move items 2 and 3 to lijie"

#[CANNOT WORK]
#Divide the bill with 2 ppl only to specific users only
"Can you divide the bill 2 ways to alice and charlie"

#[CANNOT WORK]
#Move all items from one person to another
"Can you move all items from alice to lijie"



# if its already 'divide_based" and moving item over, make sure that the values including percentage are added when moving items



'''
TODO: List needed to complete


# Make sure to have a follow up asking Are you sure
1. Delete item "__" from the list




--Divide bill by percentages

curl -X POST "http://localhost:8000/chat" \
     -H "Content-Type: application/json" \
     -d '{"message": "divide bill alice@gmail.com:30%,lijiebiz@gmail.com:30%,charlie@gmail.com:40%"}'
     


revise the splitting to be 33, 33, 34 percent if dividing the total by 3, so for all that have decimals, assign a random user to be rounded up so that the total sums to 100 percent without having decimal


'''